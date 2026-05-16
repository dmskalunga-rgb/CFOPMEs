// =====================================================
// KWANZACONTROL - Compliance Service
// Serviço de compliance e relatórios regulatórios
// Data: 2026-04-04
// =====================================================

import { supabase } from '@/integrations/supabase/client';
import { exportService } from './exportService';

export type ComplianceStandard = 'GDPR' | 'LGPD' | 'SOC2' | 'ISO27001' | 'HIPAA';

export interface ComplianceReport {
  id: string;
  tenant_id: string;
  standard: ComplianceStandard;
  report_type: 'audit' | 'assessment' | 'certification';
  period_start: string;
  period_end: string;
  status: 'draft' | 'completed' | 'certified';
  findings: ComplianceFinding[];
  score: number;
  generated_at: string;
  generated_by: string;
}

export interface ComplianceFinding {
  id: string;
  control_id: string;
  control_name: string;
  status: 'compliant' | 'non-compliant' | 'partial' | 'not-applicable';
  evidence: string[];
  notes: string;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
}

export interface DataProcessingActivity {
  id: string;
  tenant_id: string;
  activity_name: string;
  purpose: string;
  data_categories: string[];
  data_subjects: string[];
  recipients: string[];
  retention_period: string;
  security_measures: string[];
  legal_basis: string;
  created_at: string;
}

export interface DataSubjectRequest {
  id: string;
  tenant_id: string;
  request_type: 'access' | 'rectification' | 'erasure' | 'portability' | 'objection';
  subject_email: string;
  subject_name: string;
  status: 'pending' | 'in-progress' | 'completed' | 'rejected';
  requested_at: string;
  completed_at: string | null;
  notes: string;
}

export const complianceService = {
  /**
   * Gerar relatório de compliance
   */
  async generateComplianceReport(
    tenantId: string,
    standard: ComplianceStandard,
    periodStart: string,
    periodEnd: string
  ): Promise<ComplianceReport> {
    const { data, error } = await supabase.functions.invoke('compliance-generate-report', {
      body: {
        tenant_id: tenantId,
        standard,
        period_start: periodStart,
        period_end: periodEnd,
      },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Obter relatórios de compliance
   */
  async getComplianceReports(tenantId: string, standard?: ComplianceStandard): Promise<ComplianceReport[]> {
    let query = supabase
      .from('compliance_reports')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('generated_at', { ascending: false });

    if (standard) {
      query = query.eq('standard', standard);
    }

    const { data, error } = await query;
    if (error) throw error;
    return data || [];
  },

  /**
   * Obter relatório específico
   */
  async getComplianceReport(reportId: string): Promise<ComplianceReport> {
    const { data, error } = await supabase
      .from('compliance_reports')
      .select('*')
      .eq('id', reportId)
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Exportar relatório de compliance
   */
  async exportComplianceReport(reportId: string, format: 'pdf' | 'excel'): Promise<void> {
    const report = await this.getComplianceReport(reportId);

    const exportData = {
      title: `Relatório de Compliance - ${report.standard}`,
      filename: `compliance_${report.standard}_${report.period_start}_${report.period_end}`,
      headers: ['Controle', 'Status', 'Nível de Risco', 'Notas'],
      rows: report.findings.map((finding: ComplianceFinding) => [
        finding.control_name,
        finding.status,
        finding.risk_level,
        finding.notes || '-',
      ]),
    };

    if (format === 'pdf') {
      exportService.exportToPDF(exportData);
    } else {
      exportService.exportToExcel(exportData);
    }
  },

  /**
   * Registrar atividade de processamento de dados (GDPR/LGPD)
   */
  async registerDataProcessingActivity(activity: Omit<DataProcessingActivity, 'id' | 'created_at'>): Promise<DataProcessingActivity> {
    const { data, error } = await supabase
      .from('data_processing_activities')
      .insert(activity)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Obter atividades de processamento de dados
   */
  async getDataProcessingActivities(tenantId: string): Promise<DataProcessingActivity[]> {
    const { data, error } = await supabase
      .from('data_processing_activities')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data || [];
  },

  /**
   * Criar solicitação de titular de dados (GDPR/LGPD)
   */
  async createDataSubjectRequest(request: Omit<DataSubjectRequest, 'id' | 'requested_at' | 'completed_at'>): Promise<DataSubjectRequest> {
    const { data, error } = await supabase
      .from('data_subject_requests')
      .insert(request)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Obter solicitações de titulares de dados
   */
  async getDataSubjectRequests(tenantId: string, status?: string): Promise<DataSubjectRequest[]> {
    let query = supabase
      .from('data_subject_requests')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('requested_at', { ascending: false });

    if (status) {
      query = query.eq('status', status);
    }

    const { data, error } = await query;
    if (error) throw error;
    return data || [];
  },

  /**
   * Atualizar status de solicitação
   */
  async updateDataSubjectRequestStatus(
    requestId: string,
    status: DataSubjectRequest['status'],
    notes?: string
  ): Promise<void> {
    const updates: any = { status, notes };
    if (status === 'completed') {
      updates.completed_at = new Date().toISOString();
    }

    const { error } = await supabase
      .from('data_subject_requests')
      .update(updates)
      .eq('id', requestId);

    if (error) throw error;
  },

  /**
   * Obter dashboard de compliance
   */
  async getComplianceDashboard(tenantId: string): Promise<{
    overall_score: number;
    standards: Record<ComplianceStandard, { score: number; status: string }>;
    recent_findings: ComplianceFinding[];
    pending_requests: number;
    audit_trail_count: number;
  }> {
    const { data, error } = await supabase.functions.invoke('compliance-dashboard', {
      body: { tenant_id: tenantId },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Verificar conformidade de senha
   */
  checkPasswordCompliance(password: string): {
    compliant: boolean;
    issues: string[];
    strength: 'weak' | 'medium' | 'strong';
  } {
    const issues: string[] = [];
    
    if (password.length < 12) {
      issues.push('Senha deve ter no mínimo 12 caracteres');
    }
    if (!/[A-Z]/.test(password)) {
      issues.push('Senha deve conter letras maiúsculas');
    }
    if (!/[a-z]/.test(password)) {
      issues.push('Senha deve conter letras minúsculas');
    }
    if (!/[0-9]/.test(password)) {
      issues.push('Senha deve conter números');
    }
    if (!/[^A-Za-z0-9]/.test(password)) {
      issues.push('Senha deve conter caracteres especiais');
    }

    const strength = issues.length === 0 ? 'strong' : issues.length <= 2 ? 'medium' : 'weak';

    return {
      compliant: issues.length === 0,
      issues,
      strength,
    };
  },

  /**
   * Gerar relatório de auditoria para compliance
   */
  async generateAuditTrailReport(
    tenantId: string,
    startDate: string,
    endDate: string
  ): Promise<void> {
    const { data: logs, error } = await supabase
      .from('audit_logs')
      .select('*')
      .eq('tenant_id', tenantId)
      .gte('created_at', startDate)
      .lte('created_at', endDate)
      .order('created_at', { ascending: false });

    if (error) throw error;

    const exportData = exportService.prepareAuditData(logs || []);
    exportService.exportToPDF(exportData);
  },
};
