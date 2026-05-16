// =====================================================
// KWANZACONTROL - Complete Modules Service
// Serviço para módulos completos (Marketplace, Metrics, Audit)
// Data: 2026-04-08
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// MARKETPLACE SERVICE
// =====================================================

export const marketplaceService = {
  async getPlugins(tenantId: string) {
    try {
      const { data, error } = await supabase.functions.invoke('marketplace_2026_04_08', {
        body: { action: 'list', tenantId },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in marketplaceService.getPlugins:', error);
      throw error;
    }
  },

  async installPlugin(tenantId: string, pluginId: string) {
    try {
      const { data, error } = await supabase.functions.invoke('marketplace_2026_04_08', {
        body: { action: 'install', tenantId, pluginId },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in marketplaceService.installPlugin:', error);
      throw error;
    }
  },

  async uninstallPlugin(tenantId: string, pluginId: string) {
    try {
      const { data, error } = await supabase.functions.invoke('marketplace_2026_04_08', {
        body: { action: 'uninstall', tenantId, pluginId },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in marketplaceService.uninstallPlugin:', error);
      throw error;
    }
  },
};

// =====================================================
// METRICS SERVICE
// =====================================================

export const metricsService = {
  async getMetrics(tenantId: string, period = '30d') {
    try {
      const { data, error } = await supabase.functions.invoke('metrics_analytics_2026_04_08', {
        body: { tenantId, period },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in metricsService.getMetrics:', error);
      throw error;
    }
  },
};

// =====================================================
// AUDIT SERVICE
// =====================================================

export const auditService = {
  async getLogs(tenantId: string, filters?: any) {
    try {
      const { data, error } = await supabase.functions.invoke('audit_system_complete_2026_04_08', {
        body: { action: 'list', tenantId, filters },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in auditService.getLogs:', error);
      throw error;
    }
  },

  async exportLogs(tenantId: string, filters?: any) {
    try {
      const { data, error } = await supabase.functions.invoke('audit_system_complete_2026_04_08', {
        body: { action: 'export', tenantId, filters },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Error in auditService.exportLogs:', error);
      throw error;
    }
  },
};

// Exportar tudo
export default {
  marketplace: marketplaceService,
  metrics: metricsService,
  audit: auditService,
};
