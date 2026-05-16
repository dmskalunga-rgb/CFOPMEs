// =====================================================
// KWANZACONTROL - Transaction Hooks
// React Query hooks para transações
// Data: 2026-04-04
// =====================================================

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { transactionService } from '@/services';
import { useAuth } from './useAuth';
import { Database } from '@/lib/supabase-types';

type TransactionInsert = Database['public']['Tables']['transactions']['Insert'];

export function useTransactions() {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['transactions', tenant?.id],
    queryFn: () => transactionService.getAll(tenant!.id),
    enabled: !!tenant,
  });
}

export function useTransactionsByType(type: 'INCOME' | 'EXPENSE') {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['transactions', tenant?.id, 'type', type],
    queryFn: () => transactionService.getByType(tenant!.id, type),
    enabled: !!tenant && !!type,
  });
}

export function useTransactionsByPeriod(startDate: string, endDate: string) {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['transactions', tenant?.id, 'period', startDate, endDate],
    queryFn: () => transactionService.getByPeriod(tenant!.id, startDate, endDate),
    enabled: !!tenant && !!startDate && !!endDate,
  });
}

export function useCreateTransaction() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: (transaction: TransactionInsert) => transactionService.create(transaction),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions', tenant?.id] });
      queryClient.invalidateQueries({ queryKey: ['transaction-stats', tenant?.id] });
    },
  });
}

export function useUpdateTransaction() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<TransactionInsert> }) =>
      transactionService.update(id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions', tenant?.id] });
      queryClient.invalidateQueries({ queryKey: ['transaction-stats', tenant?.id] });
    },
  });
}

export function useDeleteTransaction() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: (id: string) => transactionService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions', tenant?.id] });
      queryClient.invalidateQueries({ queryKey: ['transaction-stats', tenant?.id] });
    },
  });
}

export function useClassifyTransaction() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: ({
      transactionId,
      description,
      amount,
      type,
    }: {
      transactionId: string;
      description: string;
      amount: number;
      type: string;
    }) => transactionService.classifyWithAI(transactionId, description, amount, type, tenant!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions', tenant?.id] });
    },
  });
}

export function useTransactionCategories(type?: 'INCOME' | 'EXPENSE') {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['transaction-categories', tenant?.id, type],
    queryFn: () => transactionService.getCategories(tenant!.id, type),
    enabled: !!tenant,
  });
}

export function useTransactionStats(startDate?: string, endDate?: string) {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['transaction-stats', tenant?.id, startDate, endDate],
    queryFn: () => transactionService.getStats(tenant!.id, startDate, endDate),
    enabled: !!tenant,
  });
}

export function useCashflowPrediction(monthsAhead: number = 3) {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['cashflow-prediction', tenant?.id, monthsAhead],
    queryFn: () => transactionService.predictCashflow(tenant!.id, monthsAhead),
    enabled: !!tenant,
    staleTime: 1000 * 60 * 60, // 1 hour
  });
}
