// Custom hooks for Supabase services with loading and error states
import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import {
  productsService,
  invoicesService,
  transactionsService,
  employeesService,
  projectsService,
  tasksService,
  type Product,
  type Invoice,
  type Transaction,
  type Employee,
  type Project,
  type Task,
} from '@/services/supabaseServices';

// Generic hook for data fetching
function useSupabaseData<T>(
  fetchFn: () => Promise<T>,
  deps: any[] = []
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const refetch = async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchFn();
      setData(result);
    } catch (err) {
      const error = err as Error;
      setError(error);
      toast.error(`Erro ao carregar dados: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refetch();
  }, deps);

  return { data, loading, error, refetch };
}

// Products hooks
export function useProducts() {
  return useSupabaseData<Product[]>(() => productsService.getAll());
}

export function useProduct(id: string) {
  return useSupabaseData<Product | null>(() => productsService.getById(id), [id]);
}

// Invoices hooks
export function useInvoices() {
  return useSupabaseData<Invoice[]>(() => invoicesService.getAll());
}

export function useInvoice(id: string) {
  return useSupabaseData<Invoice | null>(() => invoicesService.getById(id), [id]);
}

// Transactions hooks
export function useTransactions() {
  return useSupabaseData<Transaction[]>(() => transactionsService.getAll());
}

export function useTransactionsByDateRange(startDate: string, endDate: string) {
  return useSupabaseData<Transaction[]>(
    () => transactionsService.getByDateRange(startDate, endDate),
    [startDate, endDate]
  );
}

// Employees hooks
export function useEmployees() {
  return useSupabaseData<Employee[]>(() => employeesService.getAll());
}

export function useEmployee(id: string) {
  return useSupabaseData<Employee | null>(() => employeesService.getById(id), [id]);
}

// Projects hooks
export function useProjects() {
  return useSupabaseData<Project[]>(() => projectsService.getAll());
}

export function useProject(id: string) {
  return useSupabaseData<Project | null>(() => projectsService.getById(id), [id]);
}

// Tasks hooks
export function useTasks() {
  return useSupabaseData<Task[]>(() => tasksService.getAll());
}

export function useTask(id: string) {
  return useSupabaseData<Task | null>(() => tasksService.getById(id), [id]);
}

export function useTasksByProject(projectId: string) {
  return useSupabaseData<Task[]>(
    () => tasksService.getByProject(projectId),
    [projectId]
  );
}

// Edge Functions hooks
export function useEdgeFunctions() {
  const [loading, setLoading] = useState(false);

  const processPayroll = async (params: {
    action: 'calculate' | 'process_all' | 'approve' | 'pay';
    employee_id?: string;
    month: number;
    year: number;
    bonuses?: number;
    deductions?: number;
  }) => {
    try {
      setLoading(true);
      const { data, error } = await supabase.functions.invoke('process_payroll_2026_04_10', {
        body: params
      });
      if (error) throw error;
      toast.success('Folha de pagamento processada com sucesso!');
      return data;
    } catch (err) {
      const error = err as Error;
      toast.error(`Erro ao processar folha: ${error.message}`);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const calculateFinancials = async (params: {
    action: 'cash_flow' | 'projections' | 'roi' | 'break_even' | 'financial_health';
    start_date?: string;
    end_date?: string;
    months?: number;
    investment?: number;
    returns?: number;
    fixed_costs?: number;
    variable_cost_per_unit?: number;
    price_per_unit?: number;
  }) => {
    try {
      setLoading(true);
      const { data, error } = await supabase.functions.invoke('financial_calculations_2026_04_10', {
        body: params
      });
      if (error) throw error;
      return data;
    } catch (err) {
      const error = err as Error;
      toast.error(`Erro nos cálculos financeiros: ${error.message}`);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const generatePDF = async (params: {
    type: 'invoice' | 'payroll' | 'financial_report' | 'transaction_report';
    id?: string;
    start_date?: string;
    end_date?: string;
    month?: number;
    year?: number;
  }) => {
    try {
      setLoading(true);
      const { data, error } = await supabase.functions.invoke('generate_pdf_2026_04_10', {
        body: params
      });
      if (error) throw error;
      toast.success('PDF gerado com sucesso!');
      return data;
    } catch (err) {
      const error = err as Error;
      toast.error(`Erro ao gerar PDF: ${error.message}`);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const sendEmail = async (params: {
    type: 'invoice' | 'payroll' | 'notification' | 'report' | 'custom';
    to: string | string[];
    subject?: string;
    html?: string;
    invoice_number?: string;
    employee_name?: string;
    amount?: number;
    message?: string;
  }) => {
    try {
      setLoading(true);
      const { data, error } = await supabase.functions.invoke('send_email_2026_04_10', {
        body: params
      });
      if (error) throw error;
      toast.success('Email enviado com sucesso!');
      return data;
    } catch (err) {
      const error = err as Error;
      toast.error(`Erro ao enviar email: ${error.message}`);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  return {
    loading,
    processPayroll,
    calculateFinancials,
    generatePDF,
    sendEmail,
  };
}

// Import supabase for edge functions
import { supabase } from '@/integrations/supabase/client';
