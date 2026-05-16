// Dashboard Service - Serviço de Dashboard com dados de demonstração
import { supabase } from '@/integrations/supabase/client';

export interface DashboardMetrics {
  totalRevenue: number;
  totalExpenses: number;
  netProfit: number;
  profitMargin: number;
  activeEmployees: number;
  pendingInvoices: number;
  revenueChange: number;
  expensesChange: number;
}

export interface CashFlowData {
  month: string;
  receita: number;
  despesa: number;
}

export interface RecentActivity {
  id: string;
  type: 'invoice' | 'payment' | 'expense' | 'employee';
  description: string;
  amount?: number;
  date: string;
  status: 'success' | 'pending' | 'failed';
}

export interface Notification {
  id: string;
  type: 'warning' | 'info' | 'success' | 'error';
  title: string;
  message: string;
  date: string;
  read: boolean;
}

// Dados de demonstração
const mockMetrics: DashboardMetrics = {
  totalRevenue: 2450000,
  totalExpenses: 1680000,
  netProfit: 770000,
  profitMargin: 31.4,
  activeEmployees: 12,
  pendingInvoices: 5,
  revenueChange: 15.2,
  expensesChange: -8.5
};

const mockCashFlow: CashFlowData[] = [
  { month: 'Out/25', receita: 380000, despesa: 280000 },
  { month: 'Nov/25', receita: 420000, despesa: 290000 },
  { month: 'Dez/25', receita: 450000, despesa: 310000 },
  { month: 'Jan/26', receita: 480000, despesa: 295000 },
  { month: 'Fev/26', receita: 510000, despesa: 305000 },
  { month: 'Mar/26', receita: 540000, despesa: 320000 }
];

let mockActivities: RecentActivity[] = [
  {
    id: '1',
    type: 'invoice',
    description: 'Fatura #INV-2026-00145 criada para Empresa ABC',
    amount: 450000,
    date: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    status: 'success'
  },
  {
    id: '2',
    type: 'payment',
    description: 'Pagamento recebido de Cliente XYZ',
    amount: 320000,
    date: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString(),
    status: 'success'
  },
  {
    id: '3',
    type: 'expense',
    description: 'Despesa de fornecedor - Material de escritório',
    amount: 45000,
    date: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    status: 'success'
  },
  {
    id: '4',
    type: 'employee',
    description: 'Novo funcionário adicionado - João Silva',
    date: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    status: 'success'
  },
  {
    id: '5',
    type: 'invoice',
    description: 'Fatura #INV-2026-00144 enviada',
    amount: 280000,
    date: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
    status: 'pending'
  }
];

let mockNotifications: Notification[] = [
  {
    id: '1',
    type: 'warning',
    title: 'Faturas Pendentes',
    message: '5 faturas aguardando pagamento há mais de 30 dias',
    date: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString(),
    read: false
  },
  {
    id: '2',
    type: 'info',
    title: 'Payroll do Mês',
    message: 'Lembrete: Calcular payroll até dia 25',
    date: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    read: false
  },
  {
    id: '3',
    type: 'success',
    title: 'Meta Atingida',
    message: 'Receita mensal ultrapassou a meta em 15%',
    date: new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString(),
    read: false
  },
  {
    id: '4',
    type: 'error',
    title: 'Pagamento Atrasado',
    message: 'Fatura #INV-2026-00120 venceu há 15 dias',
    date: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    read: true
  }
];

class DashboardService {
  async getMetrics(): Promise<DashboardMetrics> {
    // Simular delay de rede
    await new Promise(resolve => setTimeout(resolve, 500));
    return mockMetrics;
  }

  async getCashFlowData(): Promise<CashFlowData[]> {
    await new Promise(resolve => setTimeout(resolve, 500));
    return mockCashFlow;
  }

  async getRecentActivities(limit = 10): Promise<RecentActivity[]> {
    await new Promise(resolve => setTimeout(resolve, 300));
    return mockActivities.slice(0, limit);
  }

  async getNotifications(unreadOnly = false): Promise<Notification[]> {
    await new Promise(resolve => setTimeout(resolve, 300));
    if (unreadOnly) {
      return mockNotifications.filter(n => !n.read);
    }
    return mockNotifications;
  }

  async markNotificationAsRead(notificationId: string): Promise<void> {
    const notification = mockNotifications.find(n => n.id === notificationId);
    if (notification) {
      notification.read = true;
    }
  }

  async markAllNotificationsAsRead(): Promise<void> {
    mockNotifications.forEach(n => n.read = true);
  }

  async addActivity(activity: Omit<RecentActivity, 'id'>): Promise<RecentActivity> {
    const newActivity: RecentActivity = {
      ...activity,
      id: String(mockActivities.length + 1)
    };
    mockActivities.unshift(newActivity);
    return newActivity;
  }

  async refreshData(): Promise<{
    metrics: DashboardMetrics;
    cashFlow: CashFlowData[];
    activities: RecentActivity[];
    notifications: Notification[];
  }> {
    await new Promise(resolve => setTimeout(resolve, 800));
    return {
      metrics: mockMetrics,
      cashFlow: mockCashFlow,
      activities: mockActivities.slice(0, 10),
      notifications: mockNotifications
    };
  }

  // Formatadores
  formatCurrency(value: number): string {
    return new Intl.NumberFormat('pt-AO', {
      style: 'currency',
      currency: 'AOA',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    }).format(value);
  }

  formatPercentage(value: number): string {
    return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;
  }

  formatDate(date: string): string {
    const d = new Date(date);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Agora';
    if (diffMins < 60) return `Há ${diffMins} min`;
    if (diffHours < 24) return `Há ${diffHours}h`;
    if (diffDays < 7) return `Há ${diffDays} dias`;
    
    return d.toLocaleDateString('pt-AO', { 
      day: '2-digit', 
      month: 'short',
      year: 'numeric'
    });
  }
}

export const dashboardService = new DashboardService();
