// =====================================================
// KWANZACONTROL - HR Service
// Serviços completos para gestão de RH
// Data: 2026-04-05
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// INTERFACES
// =====================================================

export interface Employee {
  id: string;
  tenant_id: string;
  employee_number?: string;
  nif?: string;
  bi_number?: string;
  inss_number?: string;
  full_name: string;
  email?: string;
  phone?: string;
  date_of_birth?: string;
  address?: string;
  city?: string;
  marital_status?: string;
  dependents?: number;
  emergency_contact_name?: string;
  emergency_contact_phone?: string;
  emergency_contact_relationship?: string;
  position: string;
  department?: string;
  hire_date: string;
  termination_date?: string;
  employment_type?: string;
  contract_type?: string;
  contract_start_date?: string;
  contract_end_date?: string;
  probation_end_date?: string;
  gross_salary: number;
  bank_name?: string;
  bank_account?: string;
  iban?: string;
  vacation_days_total?: number;
  vacation_days_used?: number;
  sick_days_used?: number;
  performance_score?: number;
  risk_score?: number;
  status?: string;
  documents?: any[];
  benefits?: any[];
  allowances?: any;
  notes?: string;
  created_at?: string;
  updated_at?: string;
}

export interface EmployeeDocument {
  id?: string;
  tenant_id: string;
  employee_id: string;
  document_type: string;
  document_name: string;
  document_number?: string;
  file_url: string;
  file_size?: number;
  file_type?: string;
  issue_date?: string;
  expiry_date?: string;
  is_verified?: boolean;
  verified_by?: string;
  verified_at?: string;
  notes?: string;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface EmployeeBenefit {
  id?: string;
  tenant_id: string;
  employee_id: string;
  benefit_type: string;
  benefit_name: string;
  provider?: string;
  monthly_cost?: number;
  employee_contribution?: number;
  employer_contribution?: number;
  start_date: string;
  end_date?: string;
  status?: string;
  policy_number?: string;
  notes?: string;
  created_at?: string;
  updated_at?: string;
}

export interface EmployeeAbsence {
  id?: string;
  tenant_id: string;
  employee_id: string;
  absence_type: string;
  start_date: string;
  end_date: string;
  days_count: number;
  reason?: string;
  status?: string;
  approved_by?: string;
  approved_at?: string;
  rejection_reason?: string;
  medical_certificate_url?: string;
  notes?: string;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface EmployeePerformance {
  id?: string;
  tenant_id: string;
  employee_id: string;
  evaluation_period: string;
  evaluation_date: string;
  evaluator_id?: string;
  overall_score: number;
  productivity_score?: number;
  quality_score?: number;
  teamwork_score?: number;
  punctuality_score?: number;
  initiative_score?: number;
  strengths?: string;
  weaknesses?: string;
  goals?: string;
  comments?: string;
  status?: string;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface EmployeeContract {
  id?: string;
  tenant_id: string;
  employee_id: string;
  contract_number: string;
  contract_type: string;
  start_date: string;
  end_date?: string;
  probation_period_months?: number;
  probation_end_date?: string;
  position: string;
  department?: string;
  gross_salary: number;
  work_hours_per_week?: number;
  vacation_days?: number;
  notice_period_days?: number;
  contract_file_url?: string;
  signed_by_employee?: boolean;
  signed_by_employer?: boolean;
  employee_signature?: string;
  employer_signature?: string;
  signed_at?: string;
  status?: string;
  termination_date?: string;
  termination_reason?: string;
  notes?: string;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

export interface HRAnalytics {
  id?: string;
  tenant_id: string;
  employee_id?: string;
  analysis_type: string;
  analysis_date: string;
  prediction_value?: number;
  confidence_score?: number;
  risk_level?: string;
  factors?: any[];
  recommendations?: string;
  model_version?: string;
  created_at?: string;
}

// =====================================================
// EMPLOYEE MANAGEMENT
// =====================================================

export const hrService = {
  // Buscar todos os funcionários
  async getAllEmployees(tenantId: string) {
    const { data, error } = await supabase
      .from('employees')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('full_name');

    if (error) throw error;
    return data as Employee[];
  },

  // Buscar funcionários ativos
  async getActiveEmployees(tenantId: string) {
    const { data, error } = await supabase
      .from('employees')
      .select('*')
      .eq('tenant_id', tenantId)
      .eq('status', 'ACTIVE')
      .order('full_name');

    if (error) throw error;
    return data as Employee[];
  },

  // Buscar funcionário por ID
  async getEmployeeById(id: string) {
    const { data, error } = await supabase
      .from('employees')
      .select('*')
      .eq('id', id)
      .single();

    if (error) throw error;
    return data as Employee;
  },

  // Criar funcionário
  async createEmployee(employee: Partial<Employee>) {
    const { data, error } = await supabase
      .from('employees')
      .insert(employee)
      .select()
      .single();

    if (error) throw error;
    return data as Employee;
  },

  // Atualizar funcionário
  async updateEmployee(id: string, updates: Partial<Employee>) {
    const { data, error } = await supabase
      .from('employees')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data as Employee;
  },

  // Deletar funcionário
  async deleteEmployee(id: string) {
    const { error } = await supabase
      .from('employees')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  // =====================================================
  // DOCUMENTS
  // =====================================================

  async getEmployeeDocuments(employeeId: string) {
    const { data, error } = await supabase
      .from('employee_documents')
      .select('*')
      .eq('employee_id', employeeId)
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data as EmployeeDocument[];
  },

  async createDocument(document: Partial<EmployeeDocument>) {
    const { data, error } = await supabase
      .from('employee_documents')
      .insert(document)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeDocument;
  },

  async updateDocument(id: string, updates: Partial<EmployeeDocument>) {
    const { data, error } = await supabase
      .from('employee_documents')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeDocument;
  },

  async deleteDocument(id: string) {
    const { error } = await supabase
      .from('employee_documents')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  // =====================================================
  // BENEFITS
  // =====================================================

  async getEmployeeBenefits(employeeId: string) {
    const { data, error } = await supabase
      .from('employee_benefits')
      .select('*')
      .eq('employee_id', employeeId)
      .order('start_date', { ascending: false });

    if (error) throw error;
    return data as EmployeeBenefit[];
  },

  async createBenefit(benefit: Partial<EmployeeBenefit>) {
    const { data, error } = await supabase
      .from('employee_benefits')
      .insert(benefit)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeBenefit;
  },

  async updateBenefit(id: string, updates: Partial<EmployeeBenefit>) {
    const { data, error } = await supabase
      .from('employee_benefits')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeBenefit;
  },

  async deleteBenefit(id: string) {
    const { error } = await supabase
      .from('employee_benefits')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  // =====================================================
  // ABSENCES
  // =====================================================

  async getEmployeeAbsences(employeeId: string) {
    const { data, error } = await supabase
      .from('employee_absences')
      .select('*')
      .eq('employee_id', employeeId)
      .order('start_date', { ascending: false });

    if (error) throw error;
    return data as EmployeeAbsence[];
  },

  async createAbsence(absence: Partial<EmployeeAbsence>) {
    const { data, error } = await supabase
      .from('employee_absences')
      .insert(absence)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeAbsence;
  },

  async updateAbsence(id: string, updates: Partial<EmployeeAbsence>) {
    const { data, error } = await supabase
      .from('employee_absences')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeAbsence;
  },

  async deleteAbsence(id: string) {
    const { error } = await supabase
      .from('employee_absences')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  async approveAbsence(id: string, approvedBy: string) {
    const { data, error } = await supabase
      .from('employee_absences')
      .update({
        status: 'APPROVED',
        approved_by: approvedBy,
        approved_at: new Date().toISOString(),
      })
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeAbsence;
  },

  async rejectAbsence(id: string, approvedBy: string, reason: string) {
    const { data, error } = await supabase
      .from('employee_absences')
      .update({
        status: 'REJECTED',
        approved_by: approvedBy,
        approved_at: new Date().toISOString(),
        rejection_reason: reason,
      })
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeAbsence;
  },

  // =====================================================
  // PERFORMANCE
  // =====================================================

  async getEmployeePerformance(employeeId: string) {
    const { data, error } = await supabase
      .from('employee_performance')
      .select('*')
      .eq('employee_id', employeeId)
      .order('evaluation_date', { ascending: false });

    if (error) throw error;
    return data as EmployeePerformance[];
  },

  async createPerformance(performance: Partial<EmployeePerformance>) {
    const { data, error } = await supabase
      .from('employee_performance')
      .insert(performance)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeePerformance;
  },

  async updatePerformance(id: string, updates: Partial<EmployeePerformance>) {
    const { data, error } = await supabase
      .from('employee_performance')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeePerformance;
  },

  async deletePerformance(id: string) {
    const { error } = await supabase
      .from('employee_performance')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  // =====================================================
  // CONTRACTS
  // =====================================================

  async getEmployeeContracts(employeeId: string) {
    const { data, error } = await supabase
      .from('employee_contracts')
      .select('*')
      .eq('employee_id', employeeId)
      .order('start_date', { ascending: false });

    if (error) throw error;
    return data as EmployeeContract[];
  },

  async createContract(contract: Partial<EmployeeContract>) {
    const { data, error } = await supabase
      .from('employee_contracts')
      .insert(contract)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeContract;
  },

  async updateContract(id: string, updates: Partial<EmployeeContract>) {
    const { data, error } = await supabase
      .from('employee_contracts')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data as EmployeeContract;
  },

  async deleteContract(id: string) {
    const { error } = await supabase
      .from('employee_contracts')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  // =====================================================
  // ANALYTICS (IA/ML)
  // =====================================================

  async getHRAnalytics(tenantId: string, analysisType?: string) {
    let query = supabase
      .from('hr_analytics')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('analysis_date', { ascending: false });

    if (analysisType) {
      query = query.eq('analysis_type', analysisType);
    }

    const { data, error } = await query;

    if (error) throw error;
    return data as HRAnalytics[];
  },

  async getEmployeeAnalytics(employeeId: string) {
    const { data, error } = await supabase
      .from('hr_analytics')
      .select('*')
      .eq('employee_id', employeeId)
      .order('analysis_date', { ascending: false });

    if (error) throw error;
    return data as HRAnalytics[];
  },

  // =====================================================
  // PAYROLL PROCESSING
  // =====================================================

  async processPayroll(
    tenantId: string,
    payrollMonth: string,
    employeeIds?: string[],
    options?: {
      paymentDate?: string;
      foodAllowance?: number;
      transportAllowance?: number;
      overtime?: Record<string, number>;
      bonuses?: Record<string, number>;
      commissions?: Record<string, number>;
      advances?: Record<string, number>;
      otherDeductions?: Record<string, number>;
      createTransactions?: boolean;
      enableAI?: boolean;
    }
  ) {
    const { data, error } = await supabase.functions.invoke(
      'process_payroll_intelligent_2026_04_05',
      {
        body: {
          tenantId,
          payrollMonth,
          employeeIds,
          options,
        },
      }
    );

    if (error) throw error;
    return data;
  },

  // =====================================================
  // STATISTICS
  // =====================================================

  async getHRStats(tenantId: string) {
    const { data: employees, error: empError } = await supabase
      .from('employees')
      .select('*')
      .eq('tenant_id', tenantId);

    if (empError) throw empError;

    const active = employees?.filter((e) => e.status === 'ACTIVE').length || 0;
    const inactive = employees?.filter((e) => e.status === 'INACTIVE').length || 0;
    const terminated = employees?.filter((e) => e.status === 'TERMINATED').length || 0;

    const avgSalary =
      employees && employees.length > 0
        ? employees.reduce((sum, e) => sum + parseFloat(e.gross_salary || '0'), 0) / employees.length
        : 0;

    const highRisk = employees?.filter((e) => (e.risk_score || 0) >= 0.7).length || 0;

    return {
      total: employees?.length || 0,
      active,
      inactive,
      terminated,
      avgSalary,
      highRisk,
    };
  },
};
