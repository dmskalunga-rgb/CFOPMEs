import { useQuery, useMutation, useQueryClient, UseQueryOptions } from '@tanstack/react-query';
import { supabase } from '@/integrations/supabase/client';
import { useToast } from '@/lib/toast-provider';

// Query Keys
export const queryKeys = {
  transactions: (companyId: string) => ['transactions', companyId] as const,
  transaction: (id: string) => ['transaction', id] as const,
  budgets: (companyId: string) => ['budgets', companyId] as const,
  budget: (id: string) => ['budget', id] as const,
  contracts: (companyId: string) => ['contracts', companyId] as const,
  contract: (id: string) => ['contract', id] as const,
  suppliers: (companyId: string) => ['suppliers', companyId] as const,
  customers: (companyId: string) => ['customers', companyId] as const,
  costCenters: (companyId: string) => ['costCenters', companyId] as const,
  notifications: (userId: string) => ['notifications', userId] as const,
  feedback: (companyId: string) => ['feedback', companyId] as const,
};

// Transactions Hooks
export function useTransactions(companyId: string, options?: UseQueryOptions<any[]>) {
  return useQuery({
    queryKey: queryKeys.transactions(companyId),
    queryFn: async () => {
      const { data, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('company_id', companyId)
        .order('date', { ascending: false });

      if (error) throw error;
      return data;
    },
    staleTime: 1000 * 60 * 2, // 2 minutes
    ...options,
  });
}

export function useCreateTransaction() {
  const queryClient = useQueryClient();
  const { success, error: showError } = useToast();

  return useMutation({
    mutationFn: async (transaction: any) => {
      const { data, error } = await supabase
        .from('transactions')
        .insert(transaction)
        .select()
        .single();

      if (error) throw error;
      return data;
    },
    onSuccess: (data) => {
      // Invalidate and refetch
      queryClient.invalidateQueries({ queryKey: queryKeys.transactions(data.company_id) });
      success('Sucesso', 'Transação criada com sucesso');
    },
    onError: (error: any) => {
      showError('Erro', error.message || 'Não foi possível criar a transação');
    },
  });
}

// Budgets Hooks
export function useBudgets(companyId: string) {
  return useQuery({
    queryKey: queryKeys.budgets(companyId),
    queryFn: async () => {
      const { data, error } = await supabase
        .from('budgets')
        .select('*')
        .eq('company_id', companyId)
        .order('start_date', { ascending: false });

      if (error) throw error;
      return data;
    },
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

export function useCreateBudget() {
  const queryClient = useQueryClient();
  const { success, error: showError } = useToast();

  return useMutation({
    mutationFn: async (budget: any) => {
      const { data, error } = await supabase
        .from('budgets')
        .insert(budget)
        .select()
        .single();

      if (error) throw error;
      return data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.budgets(data.company_id) });
      success('Sucesso', 'Orçamento criado com sucesso');
    },
    onError: (error: any) => {
      showError('Erro', error.message || 'Não foi possível criar o orçamento');
    },
  });
}

// Contracts Hooks
export function useContracts(companyId: string) {
  return useQuery({
    queryKey: queryKeys.contracts(companyId),
    queryFn: async () => {
      const { data, error } = await supabase
        .from('contracts')
        .select('*')
        .eq('company_id', companyId)
        .order('start_date', { ascending: false });

      if (error) throw error;
      return data;
    },
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

export function useCreateContract() {
  const queryClient = useQueryClient();
  const { success, error: showError } = useToast();

  return useMutation({
    mutationFn: async (contract: any) => {
      const { data, error } = await supabase
        .from('contracts')
        .insert(contract)
        .select()
        .single();

      if (error) throw error;
      return data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.contracts(data.company_id) });
      success('Sucesso', 'Contrato criado com sucesso');
    },
    onError: (error: any) => {
      showError('Erro', error.message || 'Não foi possível criar o contrato');
    },
  });
}

// Suppliers/Customers Hooks
export function useSuppliers(companyId: string) {
  return useQuery({
    queryKey: queryKeys.suppliers(companyId),
    queryFn: async () => {
      const { data, error } = await supabase
        .from('suppliers_customers')
        .select('*')
        .eq('company_id', companyId)
        .eq('type', 'supplier')
        .order('name');

      if (error) throw error;
      return data;
    },
    staleTime: 1000 * 60 * 10, // 10 minutes
  });
}

export function useCustomers(companyId: string) {
  return useQuery({
    queryKey: queryKeys.customers(companyId),
    queryFn: async () => {
      const { data, error } = await supabase
        .from('suppliers_customers')
        .select('*')
        .eq('company_id', companyId)
        .eq('type', 'customer')
        .order('name');

      if (error) throw error;
      return data;
    },
    staleTime: 1000 * 60 * 10, // 10 minutes
  });
}

// Notifications Hooks
export function useNotifications(userId: string) {
  return useQuery({
    queryKey: queryKeys.notifications(userId),
    queryFn: async (): Promise<any[]> => {
      // Mock data for now
      return [];
    },
    staleTime: 1000 * 60 * 1, // 1 minute
    refetchInterval: 1000 * 60 * 1, // Refetch every minute
  });
}

// Prefetch utility
export function usePrefetchQueries(companyId: string) {
  const queryClient = useQueryClient();

  const prefetchAll = () => {
    // Prefetch common queries
    queryClient.prefetchQuery({
      queryKey: queryKeys.transactions(companyId),
      queryFn: async () => {
        const { data } = await supabase
          .from('transactions')
          .select('*')
          .eq('company_id', companyId)
          .order('date', { ascending: false })
          .limit(50);
        return data;
      },
    });

    queryClient.prefetchQuery({
      queryKey: queryKeys.budgets(companyId),
      queryFn: async () => {
        const { data } = await supabase
          .from('budgets')
          .select('*')
          .eq('company_id', companyId)
          .order('start_date', { ascending: false });
        return data;
      },
    });

    queryClient.prefetchQuery({
      queryKey: queryKeys.contracts(companyId),
      queryFn: async () => {
        const { data } = await supabase
          .from('contracts')
          .select('*')
          .eq('company_id', companyId)
          .order('start_date', { ascending: false });
        return data;
      },
    });
  };

  return { prefetchAll };
}
