// =====================================================
// KWANZACONTROL - AGT & Reports Services (Mock Version)
// Serviços para AGT e Relatórios Avançados
// Versão mock sem dependência de Edge Functions
// Data: 2026-04-11
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// AGT SERVICE (Mock)
// =====================================================
export const agtService = {
  async getSettings(tenantId: string) {
    // Mock settings
    return {
      success: true,
      settings: {
        agtEnabled: false,
        apiKey: '',
        certificateId: '',
        autoSign: false,
        autoSend: false,
      },
    };
  },

  async saveSettings(tenantId: string, settings: any) {
    // Mock save
    return {
      success: true,
      message: 'Configurações salvas com sucesso',
    };
  },

  async getInvoices(tenantId: string) {
    // Mock invoices
    return {
      success: true,
      invoices: [
        {
          id: 'inv-1',
          number: 'FT 2026/001',
          date: '2026-04-01',
          customer: 'Cliente A',
          total: 50000,
          status: 'pending',
          agtStatus: 'not_sent',
        },
        {
          id: 'inv-2',
          number: 'FT 2026/002',
          date: '2026-04-05',
          customer: 'Cliente B',
          total: 75000,
          status: 'signed',
          agtStatus: 'sent',
        },
      ],
    };
  },

  async signInvoice(tenantId: string, invoiceId: string, certificateId: string) {
    // Mock sign
    return {
      success: true,
      message: 'Fatura assinada com sucesso',
      signedAt: new Date().toISOString(),
    };
  },

  async sendToAGT(tenantId: string, invoiceId: string) {
    // Mock send
    return {
      success: true,
      message: 'Fatura enviada para AGT com sucesso',
      agtCode: 'AGT-' + Math.random().toString(36).substring(7).toUpperCase(),
      sentAt: new Date().toISOString(),
    };
  },

  async validateInvoice(tenantId: string, invoiceId: string) {
    // Mock validation
    return {
      success: true,
      valid: true,
      errors: [] as any[],
      warnings: [] as any[],
    };
  },

  async getStatistics(tenantId: string) {
    // Mock statistics
    return {
      success: true,
      stats: {
        totalInvoices: 150,
        signed: 120,
        sent: 100,
        validated: 95,
        pending: 30,
        errors: 5,
      },
    };
  },

  async getLogs(tenantId: string) {
    // Mock logs
    return {
      success: true,
      logs: [
        {
          id: 'log-1',
          timestamp: new Date().toISOString(),
          action: 'sign_invoice',
          invoiceId: 'inv-1',
          status: 'success',
          message: 'Fatura assinada com sucesso',
        },
        {
          id: 'log-2',
          timestamp: new Date(Date.now() - 3600000).toISOString(),
          action: 'send_to_agt',
          invoiceId: 'inv-2',
          status: 'success',
          message: 'Fatura enviada para AGT',
        },
      ],
    };
  },
};

// =====================================================
// REPORTS SERVICE (Mock)
// =====================================================
export const reportsService = {
  async getTemplates(tenantId: string) {
    // Mock templates
    return {
      success: true,
      templates: [
        {
          id: 'tpl-1',
          name: 'Relatório Financeiro',
          description: 'Análise completa de receitas e despesas',
          category: 'financial',
          fields: ['period', 'accounts', 'categories'],
        },
        {
          id: 'tpl-2',
          name: 'Relatório de Vendas',
          description: 'Desempenho de vendas por período',
          category: 'sales',
          fields: ['period', 'products', 'customers'],
        },
        {
          id: 'tpl-3',
          name: 'Relatório de Estoque',
          description: 'Análise de inventário e movimentações',
          category: 'inventory',
          fields: ['period', 'warehouses', 'products'],
        },
      ],
    };
  },

  async generateReport(tenantId: string, userId: string, reportData: any) {
    // Mock report generation
    const reportId = 'rpt-' + Date.now();
    
    return {
      success: true,
      reportId,
      message: 'Relatório gerado com sucesso',
      report: {
        id: reportId,
        name: reportData.name || 'Relatório',
        type: reportData.type || 'financial',
        period: reportData.period || { start: '2026-01-01', end: '2026-12-31' },
        generatedAt: new Date().toISOString(),
        data: {
          summary: {
            totalRevenue: 1500000,
            totalExpense: 950000,
            profit: 550000,
            profitMargin: 0.367,
          },
          chartData: [
            { month: 'Jan', receita: 120000, despesa: 75000 },
            { month: 'Fev', receita: 135000, despesa: 82000 },
            { month: 'Mar', receita: 150000, despesa: 88000 },
            { month: 'Abr', receita: 145000, despesa: 85000 },
          ],
          topCategories: [
            { name: 'Vendas', value: 800000 },
            { name: 'Serviços', value: 500000 },
            { name: 'Outros', value: 200000 },
          ],
        },
      },
    };
  },

  async getReports(tenantId: string) {
    // Mock reports list
    return {
      success: true,
      reports: [
        {
          id: 'rpt-1',
          name: 'Relatório Financeiro Q1 2026',
          type: 'financial',
          period: { start: '2026-01-01', end: '2026-03-31' },
          generatedAt: '2026-04-01T10:00:00Z',
          status: 'completed',
          data: {
            summary: {
              totalRevenue: 405000,
              totalExpense: 245000,
              profit: 160000,
              profitMargin: 0.395,
            },
            chartData: [
              { month: 'Jan', receita: 120000, despesa: 75000 },
              { month: 'Fev', receita: 135000, despesa: 82000 },
              { month: 'Mar', receita: 150000, despesa: 88000 },
            ],
            topCategories: [
              { name: 'Vendas', value: 250000 },
              { name: 'Serviços', value: 120000 },
              { name: 'Outros', value: 35000 },
            ],
          },
        },
        {
          id: 'rpt-2',
          name: 'Relatório de Vendas Março 2026',
          type: 'sales',
          period: { start: '2026-03-01', end: '2026-03-31' },
          generatedAt: '2026-04-02T14:30:00Z',
          status: 'completed',
          data: {
            summary: {
              totalSales: 150000,
              totalOrders: 45,
              avgOrderValue: 3333,
              topProduct: 'Produto A',
            },
            chartData: [
              { week: 'Sem 1', vendas: 35000 },
              { week: 'Sem 2', vendas: 42000 },
              { week: 'Sem 3', vendas: 38000 },
              { week: 'Sem 4', vendas: 35000 },
            ],
            topProducts: [
              { name: 'Produto A', value: 50000 },
              { name: 'Produto B', value: 40000 },
              { name: 'Produto C', value: 30000 },
            ],
          },
        },
      ],
    };
  },

  async createSchedule(tenantId: string, userId: string, scheduleData: any) {
    // Mock schedule creation
    return {
      success: true,
      scheduleId: 'sch-' + Date.now(),
      message: 'Agendamento criado com sucesso',
    };
  },

  async getSchedules(tenantId: string) {
    // Mock schedules
    return {
      success: true,
      schedules: [
        {
          id: 'sch-1',
          name: 'Relatório Mensal Financeiro',
          reportType: 'financial',
          frequency: 'monthly',
          dayOfMonth: 1,
          time: '09:00',
          recipients: ['admin@empresa.com'],
          active: true,
          nextRun: '2026-05-01T09:00:00Z',
        },
        {
          id: 'sch-2',
          name: 'Relatório Semanal de Vendas',
          reportType: 'sales',
          frequency: 'weekly',
          dayOfWeek: 1,
          time: '08:00',
          recipients: ['vendas@empresa.com'],
          active: true,
          nextRun: '2026-04-14T08:00:00Z',
        },
      ],
    };
  },

  async deleteSchedule(tenantId: string, scheduleId: string) {
    // Mock delete
    return {
      success: true,
      message: 'Agendamento deletado com sucesso',
    };
  },

  async getDashboards(tenantId: string) {
    // Mock dashboards
    return {
      success: true,
      dashboards: [
        {
          id: 'dash-1',
          name: 'Dashboard Executivo',
          description: 'Visão geral do negócio',
          widgets: [
            { type: 'kpi', title: 'Receita Total', value: 1500000 },
            { type: 'chart', title: 'Vendas Mensais', chartType: 'line' },
            { type: 'table', title: 'Top Clientes' },
          ],
        },
      ],
    };
  },

  async saveDashboard(tenantId: string, userId: string, dashboardId: string | null, dashboardData: any) {
    // Mock save
    return {
      success: true,
      dashboardId: dashboardId || 'dash-' + Date.now(),
      message: 'Dashboard salvo com sucesso',
    };
  },

  async exportReport(tenantId: string, reportId: string, format: 'pdf' | 'excel' | 'csv') {
    // Mock export - use browser's print for PDF
    if (format === 'pdf') {
      // Trigger browser print dialog
      setTimeout(() => {
        window.print();
      }, 100);
      
      return {
        success: true,
        message: 'Use "Salvar como PDF" na janela de impressão',
      };
    }
    
    // For Excel/CSV, create a simple download
    const data = format === 'csv' 
      ? 'Nome,Valor\nReceita,1500000\nDespesa,950000\nLucro,550000'
      : '<table><tr><th>Nome</th><th>Valor</th></tr><tr><td>Receita</td><td>1500000</td></tr></table>';
    
    const blob = new Blob([data], { 
      type: format === 'csv' ? 'text/csv' : 'application/vnd.ms-excel' 
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `relatorio.${format}`;
    link.click();
    URL.revokeObjectURL(url);
    
    return {
      success: true,
      message: `Relatório exportado em ${format.toUpperCase()}`,
    };
  },
};
