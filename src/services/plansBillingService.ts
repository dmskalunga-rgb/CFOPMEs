// Plans & Billing Service - Serviço de Planos e Faturamento
import { supabase } from '@/integrations/supabase/client';

export interface BillingMetrics {
  totalRevenue: number;
  mrr: number;
  activeSubscriptions: number;
  churnRate: number;
  avgRevenuePerUser: number;
  growth: number;
  revenueHistory: RevenueHistoryItem[];
}

export interface RevenueHistoryItem {
  month: string;
  revenue: number;
  subscriptions: number;
}

export interface Plan {
  id: string;
  name: string;
  price: number;
  activeUsers: number;
  monthlyRevenue: number;
  marketShare: number;
  isActive: boolean;
  features: string[];
}

export interface Transaction {
  id: string;
  customerName: string;
  planName: string;
  amount: number;
  date: string;
  status: 'paid' | 'pending' | 'failed';
}

// Dados de demonstração
const mockMetrics: BillingMetrics = {
  totalRevenue: 2450000,
  mrr: 450000,
  activeSubscriptions: 127,
  churnRate: 2.3,
  avgRevenuePerUser: 3543,
  growth: 15.2,
  revenueHistory: [
    { month: 'Jan', revenue: 380000, subscriptions: 115 },
    { month: 'Fev', revenue: 395000, subscriptions: 118 },
    { month: 'Mar', revenue: 410000, subscriptions: 121 },
    { month: 'Abr', revenue: 425000, subscriptions: 124 },
    { month: 'Mai', revenue: 440000, subscriptions: 127 },
    { month: 'Jun', revenue: 450000, subscriptions: 127 }
  ]
};

const mockPlans: Plan[] = [
  {
    id: '1',
    name: 'Básico',
    price: 3000,
    activeUsers: 45,
    monthlyRevenue: 135000,
    marketShare: 35,
    isActive: true,
    features: ['5 usuários', '10GB armazenamento', 'Suporte email']
  },
  {
    id: '2',
    name: 'Profissional',
    price: 4000,
    activeUsers: 52,
    monthlyRevenue: 208000,
    marketShare: 41,
    isActive: true,
    features: ['20 usuários', '50GB armazenamento', 'Suporte prioritário', 'API access']
  },
  {
    id: '3',
    name: 'Empresarial',
    price: 9000,
    activeUsers: 30,
    monthlyRevenue: 270000,
    marketShare: 24,
    isActive: true,
    features: ['Usuários ilimitados', '500GB armazenamento', 'Suporte 24/7', 'API ilimitada', 'White label']
  }
];

const mockTransactions: Transaction[] = [
  {
    id: '1',
    customerName: 'Empresa ABC Lda',
    planName: 'Empresarial',
    amount: 9000,
    date: new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString(),
    status: 'paid'
  },
  {
    id: '2',
    customerName: 'Tech Solutions',
    planName: 'Profissional',
    amount: 4000,
    date: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    status: 'paid'
  },
  {
    id: '3',
    customerName: 'Comércio XYZ',
    planName: 'Básico',
    amount: 3000,
    date: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    status: 'pending'
  },
  {
    id: '4',
    customerName: 'Serviços Digitais',
    planName: 'Profissional',
    amount: 4000,
    date: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
    status: 'paid'
  },
  {
    id: '5',
    customerName: 'Consultoria Plus',
    planName: 'Empresarial',
    amount: 9000,
    date: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
    status: 'paid'
  },
  {
    id: '6',
    customerName: 'Startup Inovadora',
    planName: 'Básico',
    amount: 3000,
    date: new Date(Date.now() - 4 * 24 * 60 * 60 * 1000).toISOString(),
    status: 'paid'
  },
  {
    id: '7',
    customerName: 'Agência Criativa',
    planName: 'Profissional',
    amount: 4000,
    date: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
    status: 'paid'
  },
  {
    id: '8',
    customerName: 'Indústria Nacional',
    planName: 'Empresarial',
    amount: 9000,
    date: new Date(Date.now() - 6 * 24 * 60 * 60 * 1000).toISOString(),
    status: 'failed'
  }
];

class PlansBillingService {
  async getMetrics(): Promise<BillingMetrics> {
    await new Promise(resolve => setTimeout(resolve, 500));
    return mockMetrics;
  }

  async getPlans(): Promise<Plan[]> {
    await new Promise(resolve => setTimeout(resolve, 300));
    return mockPlans;
  }

  async getRecentTransactions(limit = 10): Promise<Transaction[]> {
    await new Promise(resolve => setTimeout(resolve, 300));
    return mockTransactions.slice(0, limit);
  }

  async createPlan(plan: Omit<Plan, 'id'>): Promise<Plan> {
    const newPlan: Plan = {
      ...plan,
      id: String(mockPlans.length + 1)
    };
    mockPlans.push(newPlan);
    return newPlan;
  }

  async updatePlan(id: string, updates: Partial<Plan>): Promise<Plan> {
    const index = mockPlans.findIndex(p => p.id === id);
    if (index === -1) throw new Error('Plano não encontrado');
    
    mockPlans[index] = { ...mockPlans[index], ...updates };
    return mockPlans[index];
  }

  async deletePlan(id: string): Promise<void> {
    const index = mockPlans.findIndex(p => p.id === id);
    if (index !== -1) {
      mockPlans.splice(index, 1);
    }
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

  formatDate(date: string): string {
    return new Date(date).toLocaleDateString('pt-AO', {
      day: '2-digit',
      month: 'short',
      year: 'numeric'
    });
  }

  formatPercentage(value: number): string {
    return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;
  }
}

export const plansBillingService = new PlansBillingService();
