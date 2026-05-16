// =====================================================
// KWANZACONTROL - Commercial Plans Service
// Serviço para gestão de planos comerciais
// Data: 2026-04-08
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export const commercialPlansService = {
  // Listar planos disponíveis
  async getPlans() {
    try {
      const { data, error } = await supabase.functions.invoke('commercial_plans_management_2026_04_08', {
        body: { action: 'get_plans' },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in getPlans:', error);
      throw error;
    }
  },

  // Listar módulos adicionais disponíveis
  async getAddons() {
    try {
      const { data, error } = await supabase.functions.invoke('commercial_plans_management_2026_04_08', {
        body: { action: 'get_addons' },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in getAddons:', error);
      throw error;
    }
  },

  // Obter assinatura do tenant
  async getSubscription(tenantId: string) {
    try {
      const { data, error } = await supabase.functions.invoke('commercial_plans_management_2026_04_08', {
        body: { action: 'get_subscription', tenantId },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in getSubscription:', error);
      throw error;
    }
  },

  // Adicionar módulo adicional
  async addAddon(subscriptionId: string, addonId: string, userId: string) {
    try {
      const { data, error } = await supabase.functions.invoke('commercial_plans_management_2026_04_08', {
        body: { action: 'add_addon', subscriptionId, addonId, userId },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in addAddon:', error);
      throw error;
    }
  },

  // Remover módulo adicional
  async removeAddon(subscriptionId: string, addonId: string, userId: string) {
    try {
      const { data, error } = await supabase.functions.invoke('commercial_plans_management_2026_04_08', {
        body: { action: 'remove_addon', subscriptionId, addonId, userId },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in removeAddon:', error);
      throw error;
    }
  },

  // Alterar plano
  async changePlan(subscriptionId: string, planId: string) {
    try {
      const { data, error } = await supabase.functions.invoke('commercial_plans_management_2026_04_08', {
        body: { action: 'change_plan', subscriptionId, planId },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in changePlan:', error);
      throw error;
    }
  },

  // Criar assinatura
  async createSubscription(tenantId: string, planId: string, billingCycle: string = 'monthly') {
    try {
      const { data, error } = await supabase.functions.invoke('commercial_plans_management_2026_04_08', {
        body: { action: 'create_subscription', tenantId, planId, billingCycle },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in createSubscription:', error);
      throw error;
    }
  },
};

export default commercialPlansService;
