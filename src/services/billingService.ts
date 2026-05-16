import { supabase } from '@/integrations/supabase/client';

// =====================================================
// BILLING SERVICE - Planos Comerciais
// =====================================================

export interface Plan {
  id: string;
  name: string;
  slug: string;
  description?: string;
  price_monthly: number;
  price_yearly: number;
  max_users?: number;
  max_modules?: number;
  features: string[];
  is_active: boolean;
  display_order: number;
  created_at: string;
  updated_at: string;
}

export interface Module {
  id: string;
  name: string;
  slug: string;
  description?: string;
  category?: string;
  price_monthly: number;
  price_yearly: number;
  features: string[];
  dependencies?: string[];
  is_active: boolean;
  display_order: number;
  created_at: string;
  updated_at: string;
}

export interface Subscription {
  id: string;
  organization_id: string;
  plan_id: string;
  billing_cycle: 'monthly' | 'yearly';
  status: 'trial' | 'active' | 'past_due' | 'cancelled' | 'expired';
  current_period_start: string;
  current_period_end: string;
  trial_end?: string;
  cancel_at?: string;
  cancelled_at?: string;
  created_at: string;
  updated_at: string;
}

export interface Invoice {
  id: string;
  organization_id: string;
  subscription_id?: string;
  invoice_number: string;
  amount: number;
  tax: number;
  total: number;
  currency: string;
  status: 'draft' | 'open' | 'paid' | 'void' | 'uncollectible';
  due_date: string;
  paid_at?: string;
  payment_method?: string;
  line_items: any[];
  metadata?: any;
  created_at: string;
  updated_at: string;
}

export interface Recommendation {
  id: string;
  organization_id: string;
  recommendation_type: 'upgrade' | 'downgrade' | 'add_module' | 'remove_module' | 'optimize';
  title: string;
  description?: string;
  reasoning?: string;
  potential_savings?: number;
  potential_revenue?: number;
  confidence_score: number;
  status: 'pending' | 'accepted' | 'rejected' | 'expired';
  expires_at?: string;
  created_at: string;
}

export interface ChurnPrediction {
  id: string;
  organization_id: string;
  churn_probability: number;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  factors: string[];
  recommended_actions: string[];
  predicted_at: string;
}

// =====================================================
// SUBSCRIPTION MANAGER
// =====================================================

export const billingService = {
  // =====================================================
  // PLANOS
  // =====================================================

  async getPlans(): Promise<Plan[]> {
    try {
      const { data, error } = await supabase
        .from('billing_plans')
        .select('*')
        .eq('is_active', true)
        .order('display_order', { ascending: true });

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar planos:', error);
      return [];
    }
  },

  async getPlan(planId: string): Promise<Plan | null> {
    try {
      const { data, error } = await supabase
        .from('billing_plans')
        .select('*')
        .eq('id', planId)
        .single();

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao buscar plano:', error);
      return null;
    }
  },

  // =====================================================
  // MÓDULOS
  // =====================================================

  async getModules(): Promise<Module[]> {
    try {
      const { data, error } = await supabase
        .from('billing_modules')
        .select('*')
        .eq('is_active', true)
        .order('display_order', { ascending: true });

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar módulos:', error);
      return [];
    }
  },

  async getModule(moduleId: string): Promise<Module | null> {
    try {
      const { data, error } = await supabase
        .from('billing_modules')
        .select('*')
        .eq('id', moduleId)
        .single();

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao buscar módulo:', error);
      return null;
    }
  },

  // =====================================================
  // ASSINATURAS
  // =====================================================

  async createSubscription(params: {
    organizationId: string;
    planId: string;
    billingCycle: 'monthly' | 'yearly';
  }): Promise<{ success: boolean; subscription?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_subscription_manager_2026_04_06', {
        body: {
          action: 'create',
          ...params
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao criar assinatura:', error);
      return { success: false, error: error.message };
    }
  },

  async upgradeSubscription(organizationId: string, planId: string): Promise<{ success: boolean; subscription?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_subscription_manager_2026_04_06', {
        body: {
          action: 'upgrade',
          organizationId,
          planId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao fazer upgrade:', error);
      return { success: false, error: error.message };
    }
  },

  async downgradeSubscription(organizationId: string, planId: string): Promise<{ success: boolean; subscription?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_subscription_manager_2026_04_06', {
        body: {
          action: 'downgrade',
          organizationId,
          planId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao fazer downgrade:', error);
      return { success: false, error: error.message };
    }
  },

  async cancelSubscription(organizationId: string): Promise<{ success: boolean; subscription?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_subscription_manager_2026_04_06', {
        body: {
          action: 'cancel',
          organizationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao cancelar assinatura:', error);
      return { success: false, error: error.message };
    }
  },

  async reactivateSubscription(organizationId: string): Promise<{ success: boolean; subscription?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_subscription_manager_2026_04_06', {
        body: {
          action: 'reactivate',
          organizationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao reativar assinatura:', error);
      return { success: false, error: error.message };
    }
  },

  async getSubscription(organizationId: string): Promise<{ success: boolean; subscription?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_subscription_manager_2026_04_06', {
        body: {
          action: 'get',
          organizationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao buscar assinatura:', error);
      return { success: false, error: error.message };
    }
  },

  async addModule(organizationId: string, moduleId: string): Promise<{ success: boolean; module?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_subscription_manager_2026_04_06', {
        body: {
          action: 'add_module',
          organizationId,
          moduleId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao adicionar módulo:', error);
      return { success: false, error: error.message };
    }
  },

  async removeModule(organizationId: string, moduleId: string): Promise<{ success: boolean; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_subscription_manager_2026_04_06', {
        body: {
          action: 'remove_module',
          organizationId,
          moduleId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao remover módulo:', error);
      return { success: false, error: error.message };
    }
  },

  // =====================================================
  // FATURAS
  // =====================================================

  async getInvoices(organizationId: string): Promise<Invoice[]> {
    try {
      const { data, error } = await supabase
        .from('billing_invoices')
        .select('*')
        .eq('organization_id', organizationId)
        .order('created_at', { ascending: false });

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar faturas:', error);
      return [];
    }
  },

  async getInvoice(invoiceId: string): Promise<Invoice | null> {
    try {
      const { data, error } = await supabase
        .from('billing_invoices')
        .select('*')
        .eq('id', invoiceId)
        .single();

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao buscar fatura:', error);
      return null;
    }
  },

  // =====================================================
  // RECOMENDAÇÕES DE IA
  // =====================================================

  async generateRecommendations(organizationId: string): Promise<{ success: boolean; recommendations?: any[]; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_ai_recommendations_2026_04_06', {
        body: {
          action: 'generate',
          organizationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao gerar recomendações:', error);
      return { success: false, error: error.message };
    }
  },

  async getRecommendations(organizationId: string): Promise<{ success: boolean; recommendations?: Recommendation[]; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_ai_recommendations_2026_04_06', {
        body: {
          action: 'list',
          organizationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao buscar recomendações:', error);
      return { success: false, error: error.message };
    }
  },

  async acceptRecommendation(recommendationId: string): Promise<{ success: boolean; recommendation?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_ai_recommendations_2026_04_06', {
        body: {
          action: 'accept',
          recommendationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao aceitar recomendação:', error);
      return { success: false, error: error.message };
    }
  },

  async rejectRecommendation(recommendationId: string): Promise<{ success: boolean; recommendation?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_ai_recommendations_2026_04_06', {
        body: {
          action: 'reject',
          recommendationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao rejeitar recomendação:', error);
      return { success: false, error: error.message };
    }
  },

  // =====================================================
  // PREDIÇÃO DE CHURN
  // =====================================================

  async predictChurn(organizationId: string): Promise<{ success: boolean; prediction?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_churn_predictor_2026_04_06', {
        body: {
          action: 'predict',
          organizationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao prever churn:', error);
      return { success: false, error: error.message };
    }
  },

  async getChurnPrediction(organizationId: string): Promise<{ success: boolean; prediction?: ChurnPrediction; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_churn_predictor_2026_04_06', {
        body: {
          action: 'get',
          organizationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao buscar predição de churn:', error);
      return { success: false, error: error.message };
    }
  },

  async listHighRiskOrganizations(): Promise<{ success: boolean; organizations?: any[]; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('billing_churn_predictor_2026_04_06', {
        body: {
          action: 'list_high_risk'
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao listar organizações em risco:', error);
      return { success: false, error: error.message };
    }
  }
};
