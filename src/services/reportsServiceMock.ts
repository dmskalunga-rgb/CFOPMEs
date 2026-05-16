// Reports Service - Mock Version (sem Edge Functions)
import { supabase } from '@/integrations/supabase/client';

// Types
export interface ReportTemplate {
  id: string;
  name: string;
  description?: string;
  category: string;
  template_type: string;
  data_sources?: any;
  parameters?: any;
  layout_config?: any;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface GeneratedReport {
  id: string;
  template_id?: string;
  name: string;
  description?: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  file_url?: string;
  file_size?: number;
  file_format: string;
  generation_time?: number;
  parameters_used?: any;
  data_snapshot?: any;
  error_message?: string;
  generated_at: string;
  download_count: number;
  last_downloaded_at?: string;
  template?: {
    name: string;
    category: string;
    description?: string;
  };
}

export interface ReportsStats {
  total_reports: number;
  reports_this_month: number;
  completed_reports: number;
  pending_reports: number;
  failed_reports: number;
  total_size: number;
  avg_generation_time: number;
  total_downloads: number;
}

class ReportsServiceMock {
  // Mock data
  private mockTemplates: ReportTemplate[] = [
    {
      id: 'tpl-1',
      name: 'Relatório Financeiro Mensal',
      description: 'Análise completa de receitas, despesas e lucros',
      category: 'Financeiro',
      template_type: 'financial',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 'tpl-2',
      name: 'Relatório de Vendas',
      description: 'Desempenho de vendas por produto e cliente',
      category: 'Vendas',
      template_type: 'sales',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 'tpl-3',
      name: 'Relatório de Estoque',
      description: 'Análise de inventário e movimentações',
      category: 'Estoque',
      template_type: 'inventory',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ];

  private mockReports: GeneratedReport[] = [
    {
      id: 'rpt-1',
      template_id: 'tpl-1',
      name: 'Relatório Financeiro - Março 2026',
      status: 'completed',
      file_format: 'pdf',
      file_size: 524288,
      generation_time: 5,
      generated_at: '2026-04-01T10:00:00Z',
      download_count: 3,
      template: {
        name: 'Relatório Financeiro Mensal',
        category: 'Financeiro',
      },
    },
    {
      id: 'rpt-2',
      template_id: 'tpl-2',
      name: 'Relatório de Vendas - Q1 2026',
      status: 'completed',
      file_format: 'excel',
      file_size: 1048576,
      generation_time: 8,
      generated_at: '2026-04-05T14:30:00Z',
      download_count: 5,
      template: {
        name: 'Relatório de Vendas',
        category: 'Vendas',
      },
    },
  ];

  // ===== TEMPLATES =====
  async listTemplates(): Promise<ReportTemplate[]> {
    // Simulate API delay
    await new Promise(resolve => setTimeout(resolve, 500));
    return this.mockTemplates;
  }

  async getTemplate(id: string): Promise<ReportTemplate> {
    await new Promise(resolve => setTimeout(resolve, 300));
    const template = this.mockTemplates.find(t => t.id === id);
    if (!template) throw new Error('Template não encontrado');
    return template;
  }

  // ===== REPORTS =====
  async listReports(filters?: { status?: string; limit?: number }): Promise<GeneratedReport[]> {
    await new Promise(resolve => setTimeout(resolve, 500));
    let reports = [...this.mockReports];
    
    if (filters?.status) {
      reports = reports.filter(r => r.status === filters.status);
    }
    
    if (filters?.limit) {
      reports = reports.slice(0, filters.limit);
    }
    
    return reports;
  }

  async getReport(id: string): Promise<GeneratedReport> {
    await new Promise(resolve => setTimeout(resolve, 300));
    const report = this.mockReports.find(r => r.id === id);
    if (!report) throw new Error('Relatório não encontrado');
    return report;
  }

  async generateReport(templateId: string, name?: string, parameters?: any): Promise<GeneratedReport> {
    await new Promise(resolve => setTimeout(resolve, 2000)); // Simulate generation time
    
    const template = this.mockTemplates.find(t => t.id === templateId);
    if (!template) throw new Error('Template não encontrado');
    
    const newReport: GeneratedReport = {
      id: 'rpt-' + Date.now(),
      template_id: templateId,
      name: name || `${template.name} - ${new Date().toLocaleDateString('pt-AO')}`,
      status: 'completed',
      file_format: 'pdf',
      file_size: Math.floor(Math.random() * 1000000) + 100000,
      generation_time: Math.floor(Math.random() * 10) + 3,
      generated_at: new Date().toISOString(),
      download_count: 0,
      template: {
        name: template.name,
        category: template.category,
        description: template.description,
      },
    };
    
    this.mockReports.unshift(newReport);
    return newReport;
  }

  async downloadReport(id: string): Promise<{ file_url: string; file_name: string; file_size: number }> {
    await new Promise(resolve => setTimeout(resolve, 500));
    
    const report = this.mockReports.find(r => r.id === id);
    if (!report) throw new Error('Relatório não encontrado');
    
    // Update download count
    report.download_count++;
    report.last_downloaded_at = new Date().toISOString();
    
    // Trigger browser print for PDF
    if (report.file_format === 'pdf') {
      setTimeout(() => {
        window.print();
      }, 100);
    }
    
    return {
      file_url: '#',
      file_name: `${report.name}.${report.file_format}`,
      file_size: report.file_size || 0,
    };
  }

  async deleteReport(id: string): Promise<void> {
    await new Promise(resolve => setTimeout(resolve, 300));
    const index = this.mockReports.findIndex(r => r.id === id);
    if (index === -1) throw new Error('Relatório não encontrado');
    this.mockReports.splice(index, 1);
  }

  // ===== STATS =====
  async getStats(): Promise<ReportsStats> {
    await new Promise(resolve => setTimeout(resolve, 300));
    
    const now = new Date();
    const thisMonth = this.mockReports.filter(r => {
      const reportDate = new Date(r.generated_at);
      return reportDate.getMonth() === now.getMonth() && 
             reportDate.getFullYear() === now.getFullYear();
    });
    
    return {
      total_reports: this.mockReports.length,
      reports_this_month: thisMonth.length,
      completed_reports: this.mockReports.filter(r => r.status === 'completed').length,
      pending_reports: this.mockReports.filter(r => r.status === 'pending').length,
      failed_reports: this.mockReports.filter(r => r.status === 'failed').length,
      total_size: this.mockReports.reduce((sum, r) => sum + (r.file_size || 0), 0),
      avg_generation_time: this.mockReports.reduce((sum, r) => sum + (r.generation_time || 0), 0) / this.mockReports.length,
      total_downloads: this.mockReports.reduce((sum, r) => sum + r.download_count, 0),
    };
  }

  // ===== HELPERS =====
  formatFileSize(bytes: number): string {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  }

  formatGenerationTime(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}m ${remainingSeconds}s`;
  }

  getStatusLabel(status: string): string {
    const labels: Record<string, string> = {
      pending: 'Pendente',
      processing: 'Processando',
      completed: 'Concluído',
      failed: 'Falhou',
    };
    return labels[status] || status;
  }

  getStatusColor(status: string): string {
    const colors: Record<string, string> = {
      pending: 'bg-yellow-50 text-yellow-700 border-yellow-200',
      processing: 'bg-blue-50 text-blue-700 border-blue-200',
      completed: 'bg-green-50 text-green-700 border-green-200',
      failed: 'bg-red-50 text-red-700 border-red-200',
    };
    return colors[status] || 'bg-gray-50 text-gray-700 border-gray-200';
  }
}

export const reportsService = new ReportsServiceMock();
