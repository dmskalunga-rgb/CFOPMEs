// =====================================================
// KWANZACONTROL - Payroll Hooks
// React Query hooks para folha de pagamento
// Data: 2026-04-04
// =====================================================

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { payrollService } from '@/services';
import { useAuth } from './useAuth';
import { Database } from '@/lib/supabase-types';

type EmployeeInsert = Database['public']['Tables']['employees']['Insert'];
type PayslipInsert = Database['public']['Tables']['payslips']['Insert'];

export function useEmployees() {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['employees', tenant?.id],
    queryFn: () => payrollService.getAllEmployees(tenant!.id),
    enabled: !!tenant,
  });
}

export function useActiveEmployees() {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['employees', tenant?.id, 'active'],
    queryFn: () => payrollService.getActiveEmployees(tenant!.id),
    enabled: !!tenant,
  });
}

export function useCreateEmployee() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: (employee: EmployeeInsert) => payrollService.createEmployee(employee),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['employees', tenant?.id] });
    },
  });
}

export function useUpdateEmployee() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<EmployeeInsert> }) =>
      payrollService.updateEmployee(id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['employees', tenant?.id] });
    },
  });
}

export function useDeleteEmployee() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: (id: string) => payrollService.deleteEmployee(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['employees', tenant?.id] });
    },
  });
}

export function usePayslips(month?: string) {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['payslips', tenant?.id, month],
    queryFn: () => payrollService.getPayslips(tenant!.id, month),
    enabled: !!tenant,
  });
}

export function usePayslip(id: string) {
  return useQuery({
    queryKey: ['payslip', id],
    queryFn: () => payrollService.getPayslipById(id),
    enabled: !!id,
  });
}

export function useCalculatePayroll() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: ({ payrollMonth, employeeIds }: { payrollMonth: string; employeeIds?: string[] }) =>
      payrollService.calculatePayroll(tenant!.id, payrollMonth, employeeIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['payslips', tenant?.id] });
      queryClient.invalidateQueries({ queryKey: ['payroll-stats', tenant?.id] });
    },
  });
}

export function useCreatePayslip() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: (payslip: PayslipInsert) => payrollService.createPayslip(payslip),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['payslips', tenant?.id] });
    },
  });
}

export function useUpdatePayslip() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<PayslipInsert> }) =>
      payrollService.updatePayslip(id, updates),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['payslips', tenant?.id] });
      queryClient.invalidateQueries({ queryKey: ['payslip', variables.id] });
    },
  });
}

export function usePayrollStats(month?: string) {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['payroll-stats', tenant?.id, month],
    queryFn: () => payrollService.getStats(tenant!.id, month),
    enabled: !!tenant,
  });
}

export function useEmployeesByDepartment(department: string) {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['employees', tenant?.id, 'department', department],
    queryFn: () => payrollService.getByDepartment(tenant!.id, department),
    enabled: !!tenant && !!department,
  });
}
