// =====================================================
// KWANZACONTROL - Invoice Hooks
// React Query hooks para faturas
// Data: 2026-04-04
// =====================================================

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { invoiceService } from '@/services';
import { useAuth } from './useAuth';
import { Database } from '@/lib/supabase-types';

type InvoiceInsert = Database['public']['Tables']['invoices']['Insert'];
type InvoiceItemInsert = Database['public']['Tables']['invoice_items']['Insert'];

export function useInvoices() {
  const { tenant } = useAuth();
  
  return useQuery({
    queryKey: ['invoices', tenant?.id],
    queryFn: () => invoiceService.getAll(tenant!.id),
    enabled: !!tenant,
  });
}

export function useInvoice(id: string) {
  return useQuery({
    queryKey: ['invoice', id],
    queryFn: () => invoiceService.getById(id),
    enabled: !!id,
  });
}

export function useCreateInvoice() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: ({ invoice, items }: { invoice: InvoiceInsert; items: Omit<InvoiceItemInsert, 'invoice_id'>[] }) =>
      invoiceService.create(invoice, items),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['invoices', tenant?.id] });
    },
  });
}

export function useUpdateInvoice() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<InvoiceInsert> }) =>
      invoiceService.update(id, updates),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['invoices', tenant?.id] });
      queryClient.invalidateQueries({ queryKey: ['invoice', variables.id] });
    },
  });
}

export function useDeleteInvoice() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: (id: string) => invoiceService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['invoices', tenant?.id] });
    },
  });
}

export function useSubmitInvoiceToAGT() {
  const queryClient = useQueryClient();
  const { tenant } = useAuth();

  return useMutation({
    mutationFn: (invoiceId: string) => invoiceService.submitToAGT(invoiceId, tenant!.id),
    onSuccess: (_, invoiceId) => {
      queryClient.invalidateQueries({ queryKey: ['invoices', tenant?.id] });
      queryClient.invalidateQueries({ queryKey: ['invoice', invoiceId] });
    },
  });
}

export function useInvoiceStats() {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['invoice-stats', tenant?.id],
    queryFn: () => invoiceService.getStats(tenant!.id),
    enabled: !!tenant,
  });
}

export function useInvoicesByStatus(status: string) {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['invoices', tenant?.id, 'status', status],
    queryFn: () => invoiceService.getByStatus(tenant!.id, status),
    enabled: !!tenant && !!status,
  });
}

export function useInvoicesByPeriod(startDate: string, endDate: string) {
  const { tenant } = useAuth();

  return useQuery({
    queryKey: ['invoices', tenant?.id, 'period', startDate, endDate],
    queryFn: () => invoiceService.getByPeriod(tenant!.id, startDate, endDate),
    enabled: !!tenant && !!startDate && !!endDate,
  });
}
