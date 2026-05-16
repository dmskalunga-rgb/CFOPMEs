// =====================================================
// KWANZACONTROL - Payroll Service
// Serviços para gestão de folha de pagamento
// Data: 2026-04-04
// =====================================================

import { supabase } from '@/integrations/supabase/client';
import { Database } from '@/lib/supabase-types';

type Employee = Database['public']['Tables']['employees']['Row'];
type EmployeeInsert = Database['public']['Tables']['employees']['Insert'];
type Payslip = Database['public']['Tables']['payslips']['Row'];
type PayslipInsert = Database['public']['Tables']['payslips']['Insert'];

export const payrollService = {
  /**
   * Buscar todos os funcionários
   */
  async getAllEmployees(tenantId: string) {
    const { data, error } = await supabase
      .from('employees')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('full_name');

    if (error) throw error;
    return data;
  },

  /**
   * Buscar funcionários ativos
   */
  async getActiveEmployees(tenantId: string) {
    const { data, error } = await supabase
      .from('employees')
      .select('*')
      .eq('tenant_id', tenantId)
      .eq('status', 'ACTIVE')
      .order('full_name');

    if (error) throw error;
    return data;
  },

  /**
   * Criar funcionário
   */
  async createEmployee(employee: EmployeeInsert) {
    const { data, error } = await supabase
      .from('employees')
      .insert(employee)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Atualizar funcionário
   */
  async updateEmployee(id: string, updates: Partial<EmployeeInsert>) {
    const { data, error } = await supabase
      .from('employees')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Deletar funcionário
   */
  async deleteEmployee(id: string) {
    const { error } = await supabase
      .from('employees')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  /**
   * Buscar recibos de salário
   */
  async getPayslips(tenantId: string, month?: string) {
    let query = supabase
      .from('payslips')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('payroll_month', { ascending: false });

    if (month) {
      query = query.eq('payroll_month', month);
    }

    const { data, error } = await query;

    if (error) throw error;
    return data;
  },

  /**
   * Buscar recibo por ID
   */
  async getPayslipById(id: string) {
    const { data, error } = await supabase
      .from('payslips')
      .select('*')
      .eq('id', id)
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Calcular folha de pagamento
   */
  async calculatePayroll(tenantId: string, payrollMonth: string, employeeIds?: string[]) {
    const { data, error } = await supabase.functions.invoke(
      'calculate_payroll_2026_04_04',
      {
        body: {
          tenant_id: tenantId,
          payroll_month: payrollMonth,
          employee_ids: employeeIds,
        },
      }
    );

    if (error) throw error;
    return data;
  },

  /**
   * Criar recibo manualmente
   */
  async createPayslip(payslip: PayslipInsert) {
    const { data, error } = await supabase
      .from('payslips')
      .insert(payslip)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Atualizar recibo
   */
  async updatePayslip(id: string, updates: Partial<PayslipInsert>) {
    const { data, error } = await supabase
      .from('payslips')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Buscar estatísticas de payroll
   */
  async getStats(tenantId: string, month?: string) {
    let query = supabase
      .from('payslips')
      .select('*')
      .eq('tenant_id', tenantId);

    if (month) {
      query = query.eq('payroll_month', month);
    }

    const { data, error } = await query;

    if (error) throw error;

    const stats = {
      totalEmployees: new Set(data.map(p => p.employee_id)).size,
      totalGross: data.reduce((sum, p) => sum + Number(p.gross_salary), 0),
      totalNet: data.reduce((sum, p) => sum + Number(p.net_salary), 0),
      totalINSSEmployee: data.reduce((sum, p) => sum + Number(p.inss_employee), 0),
      totalINSSEmployer: data.reduce((sum, p) => sum + Number(p.inss_employer), 0),
      totalIRT: data.reduce((sum, p) => sum + Number(p.irt), 0),
      totalDeductions: data.reduce((sum, p) => sum + Number(p.total_deductions), 0),
      pending: data.filter(p => p.payment_status === 'PENDING').length,
      paid: data.filter(p => p.payment_status === 'PAID').length,
    };

    return stats;
  },

  /**
   * Buscar funcionários por departamento
   */
  async getByDepartment(tenantId: string, department: string) {
    const { data, error } = await supabase
      .from('employees')
      .select('*')
      .eq('tenant_id', tenantId)
      .eq('department', department)
      .eq('status', 'ACTIVE')
      .order('full_name');

    if (error) throw error;
    return data;
  },
};
