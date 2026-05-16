// =====================================================
// KWANZACONTROL - Invoice Service
// Serviços para gestão de faturas com Supabase
// Data: 2026-04-04
// =====================================================

import { supabase } from '@/integrations/supabase/client';
import { Database } from '@/lib/supabase-types';

type Invoice = Database['public']['Tables']['invoices']['Row'];
type InvoiceInsert = Database['public']['Tables']['invoices']['Insert'];
type InvoiceItem = Database['public']['Tables']['invoice_items']['Row'];
type InvoiceItemInsert = Database['public']['Tables']['invoice_items']['Insert'];
type Customer = Database['public']['Tables']['customers']['Row'];

export interface InvoiceWithItems extends Invoice {
  invoice_items: InvoiceItem[];
  customers: Customer | null;
}

export const invoiceService = {
  /**
   * Buscar todas as faturas do tenant
   */
  async getAll(tenantId: string) {
    const { data, error } = await supabase
      .from('invoices')
      .select(`
        *,
        invoice_items (*),
        customers (*)
      `)
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data as InvoiceWithItems[];
  },

  /**
   * Buscar fatura por ID
   */
  async getById(id: string) {
    const { data, error } = await supabase
      .from('invoices')
      .select(`
        *,
        invoice_items (*),
        customers (*)
      `)
      .eq('id', id)
      .single();

    if (error) throw error;
    return data as InvoiceWithItems;
  },

  /**
   * Criar nova fatura
   */
  async create(invoice: InvoiceInsert, items: Omit<InvoiceItemInsert, 'invoice_id'>[]) {
    // 1. Criar fatura
    const { data: newInvoice, error: invoiceError } = await supabase
      .from('invoices')
      .insert(invoice)
      .select()
      .single();

    if (invoiceError) throw invoiceError;

    // 2. Criar itens
    const itemsWithInvoiceId = items.map((item, index) => ({
      ...item,
      invoice_id: newInvoice.id,
      line_number: index + 1,
    }));

    const { error: itemsError } = await supabase
      .from('invoice_items')
      .insert(itemsWithInvoiceId);

    if (itemsError) throw itemsError;

    // 3. Buscar fatura completa
    return this.getById(newInvoice.id);
  },

  /**
   * Atualizar fatura
   */
  async update(id: string, updates: Partial<InvoiceInsert>) {
    const { data, error } = await supabase
      .from('invoices')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Deletar fatura
   */
  async delete(id: string) {
    const { error } = await supabase
      .from('invoices')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  /**
   * Enviar fatura para AGT
   */
  async submitToAGT(invoiceId: string, tenantId: string) {
    const { data, error } = await supabase.functions.invoke(
      'agt_submit_invoice_2026_04_04',
      {
        body: {
          invoice_id: invoiceId,
          tenant_id: tenantId,
        },
      }
    );

    if (error) throw error;
    return data;
  },

  /**
   * Buscar faturas por status
   */
  async getByStatus(tenantId: string, status: string) {
    const { data, error } = await supabase
      .from('invoices')
      .select(`
        *,
        invoice_items (*),
        customers (*)
      `)
      .eq('tenant_id', tenantId)
      .eq('status', status)
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data as InvoiceWithItems[];
  },

  /**
   * Buscar faturas por período
   */
  async getByPeriod(tenantId: string, startDate: string, endDate: string) {
    const { data, error } = await supabase
      .from('invoices')
      .select(`
        *,
        invoice_items (*),
        customers (*)
      `)
      .eq('tenant_id', tenantId)
      .gte('issue_date', startDate)
      .lte('issue_date', endDate)
      .order('issue_date', { ascending: false });

    if (error) throw error;
    return data as InvoiceWithItems[];
  },

  /**
   * Buscar estatísticas de faturas
   */
  async getStats(tenantId: string) {
    const { data, error } = await supabase
      .from('invoices')
      .select('status, total')
      .eq('tenant_id', tenantId);

    if (error) throw error;

    const stats = {
      total: data.length,
      draft: data.filter(i => i.status === 'DRAFT').length,
      sent: data.filter(i => i.status === 'SENT').length,
      paid: data.filter(i => i.status === 'PAID').length,
      overdue: data.filter(i => i.status === 'OVERDUE').length,
      totalAmount: data.reduce((sum, i) => sum + Number(i.total), 0),
      paidAmount: data.filter(i => i.status === 'PAID').reduce((sum, i) => sum + Number(i.total), 0),
    };

    return stats;
  },
};
