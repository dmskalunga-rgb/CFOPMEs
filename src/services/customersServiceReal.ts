// Real Supabase Service for Customers
import { supabase } from '@/integrations/supabase/client';

export interface Customer {
  id: string;
  name: string;
  email: string;
  phone?: string;
  company?: string;
  address?: string;
  city?: string;
  country: string;
  tax_id?: string;
  customer_status: 'active' | 'inactive';
  total_purchases: number;
  created_at: string;
  updated_at: string;
}

export const customersService = {
  // Get all customers
  async getAll(): Promise<Customer[]> {
    const { data, error } = await supabase
      .from('customers_2026_04_09')
      .select('*')
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data || [];
  },

  // Get customer by ID
  async getById(id: string): Promise<Customer | null> {
    const { data, error } = await supabase
      .from('customers_2026_04_09')
      .select('*')
      .eq('id', id)
      .single();

    if (error) throw error;
    return data;
  },

  // Create customer
  async create(customer: Omit<Customer, 'id' | 'created_at' | 'updated_at'>): Promise<Customer> {
    const { data, error } = await supabase
      .from('customers_2026_04_09')
      .insert(customer)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Update customer
  async update(id: string, updates: Partial<Customer>): Promise<Customer> {
    const { data, error } = await supabase
      .from('customers_2026_04_09')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Delete customer
  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('customers_2026_04_09')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  // Search customers
  async search(query: string): Promise<Customer[]> {
    const { data, error } = await supabase
      .from('customers_2026_04_09')
      .select('*')
      .or(`name.ilike.%${query}%,email.ilike.%${query}%,company.ilike.%${query}%`)
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data || [];
  },
};
