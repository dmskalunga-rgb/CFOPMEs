// =====================================================
// KWANZACONTROL - AGT & Reports Services
// Serviços para AGT e Relatórios Avançados
// Data: 2026-04-08
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// AGT SERVICE
// =====================================================
export const agtService = {
  async getSettings(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('agt_integration_complete_2026_04_08', {
      body: { action: 'get_settings', tenantId },
    });
    if (error) throw error;
    return data;
  },

  async saveSettings(tenantId: string, settings: any) {
    const { data, error } = await supabase.functions.invoke('agt_integration_complete_2026_04_08', {
      body: { action: 'save_settings', tenantId, settings },
    });
    if (error) throw error;
    return data;
  },

  async getInvoices(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('agt_integration_complete_2026_04_08', {
      body: { action: 'get_invoices', tenantId },
    });
    if (error) throw error;
    return data;
  },

  async signInvoice(tenantId: string, invoiceId: string, certificateId: string) {
    const { data, error } = await supabase.functions.invoke('agt_integration_complete_2026_04_08', {
      body: { action: 'sign_invoice', tenantId, invoiceId, certificateId },
    });
    if (error) throw error;
    return data;
  },

  async sendToAGT(tenantId: string, invoiceId: string) {
    const { data, error } = await supabase.functions.invoke('agt_integration_complete_2026_04_08', {
      body: { action: 'send_to_agt', tenantId, invoiceId },
    });
    if (error) throw error;
    return data;
  },

  async validateInvoice(tenantId: string, invoiceId: string) {
    const { data, error } = await supabase.functions.invoke('agt_integration_complete_2026_04_08', {
      body: { action: 'validate_invoice', tenantId, invoiceId },
    });
    if (error) throw error;
    return data;
  },

  async getStatistics(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('agt_integration_complete_2026_04_08', {
      body: { action: 'get_statistics', tenantId },
    });
    if (error) throw error;
    return data;
  },

  async getLogs(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('agt_integration_complete_2026_04_08', {
      body: { action: 'get_logs', tenantId },
    });
    if (error) throw error;
    return data;
  },
};

// =====================================================
// REPORTS SERVICE
// =====================================================
export const reportsService = {
  async getTemplates(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'get_templates', tenantId },
    });
    if (error) throw error;
    return data;
  },

  async generateReport(tenantId: string, userId: string, reportData: any) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'generate_report', tenantId, userId, data: reportData },
    });
    if (error) throw error;
    return data;
  },

  async getReports(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'get_reports', tenantId },
    });
    if (error) throw error;
    return data;
  },

  async createSchedule(tenantId: string, userId: string, scheduleData: any) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'create_schedule', tenantId, userId, data: scheduleData },
    });
    if (error) throw error;
    return data;
  },

  async getSchedules(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'get_schedules', tenantId },
    });
    if (error) throw error;
    return data;
  },

  async deleteSchedule(tenantId: string, scheduleId: string) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'delete_schedule', tenantId, scheduleId },
    });
    if (error) throw error;
    return data;
  },

  async getDashboards(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'get_dashboards', tenantId },
    });
    if (error) throw error;
    return data;
  },

  async saveDashboard(tenantId: string, userId: string, dashboardId: string | null, dashboardData: any) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'save_dashboard', tenantId, userId, dashboardId, data: dashboardData },
    });
    if (error) throw error;
    return data;
  },

  async exportReport(tenantId: string, userId: string, exportData: any) {
    const { data, error } = await supabase.functions.invoke('advanced_reports_complete_2026_04_08', {
      body: { action: 'export_report', tenantId, userId, data: exportData },
    });
    if (error) throw error;
    return data;
  },
};

export default { agtService, reportsService };
