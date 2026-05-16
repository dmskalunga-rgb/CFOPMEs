// Real Supabase Services - Complete CRUD operations
import { supabase } from '@/integrations/supabase/client';

// ============================================
// PRODUCTS SERVICE
// ============================================
export interface Product {
  id: string;
  name: string;
  sku: string;
  description?: string;
  category: string;
  price: number;
  cost?: number;
  stock: number;
  min_stock: number;
  max_stock?: number;
  unit: string;
  product_status: 'active' | 'inactive' | 'discontinued';
  created_at: string;
  updated_at: string;
}

export const productsService = {
  async getAll(): Promise<Product[]> {
    const { data, error } = await supabase
      .from('products_2026_04_09')
      .select('*')
      .order('created_at', { ascending: false });
    if (error) throw error;
    return data || [];
  },

  async getById(id: string): Promise<Product | null> {
    const { data, error } = await supabase
      .from('products_2026_04_09')
      .select('*')
      .eq('id', id)
      .single();
    if (error) throw error;
    return data;
  },

  async create(product: Omit<Product, 'id' | 'created_at' | 'updated_at'>): Promise<Product> {
    const { data, error } = await supabase
      .from('products_2026_04_09')
      .insert(product)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: Partial<Product>): Promise<Product> {
    const { data, error } = await supabase
      .from('products_2026_04_09')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('products_2026_04_09')
      .delete()
      .eq('id', id);
    if (error) throw error;
  },

  async search(query: string): Promise<Product[]> {
    const { data, error } = await supabase
      .from('products_2026_04_09')
      .select('*')
      .or(`name.ilike.%${query}%,sku.ilike.%${query}%,category.ilike.%${query}%`)
      .order('created_at', { ascending: false });
    if (error) throw error;
    return data || [];
  },
};

// ============================================
// INVOICES SERVICE
// ============================================
export interface Invoice {
  id: string;
  invoice_number: string;
  customer_id?: string;
  customer_name: string;
  customer_email?: string;
  issue_date: string;
  due_date: string;
  subtotal: number;
  tax_rate: number;
  tax_amount: number;
  total: number;
  invoice_status: 'draft' | 'sent' | 'paid' | 'overdue' | 'cancelled';
  notes?: string;
  created_at: string;
  updated_at: string;
}

export interface InvoiceItem {
  id: string;
  invoice_id: string;
  product_id?: string;
  description: string;
  quantity: number;
  unit_price: number;
  total: number;
}

export const invoicesService = {
  async getAll(): Promise<Invoice[]> {
    const { data, error } = await supabase
      .from('invoices_2026_04_09')
      .select('*')
      .order('issue_date', { ascending: false });
    if (error) throw error;
    return data || [];
  },

  async getById(id: string): Promise<Invoice | null> {
    const { data, error } = await supabase
      .from('invoices_2026_04_09')
      .select('*')
      .eq('id', id)
      .single();
    if (error) throw error;
    return data;
  },

  async getItems(invoiceId: string): Promise<InvoiceItem[]> {
    const { data, error } = await supabase
      .from('invoice_items_2026_04_09')
      .select('*')
      .eq('invoice_id', invoiceId);
    if (error) throw error;
    return data || [];
  },

  async create(invoice: Omit<Invoice, 'id' | 'created_at' | 'updated_at'>, items: Omit<InvoiceItem, 'id' | 'invoice_id'>[]): Promise<Invoice> {
    // Use Edge Function for complex invoice creation
    const { data, error } = await supabase.functions.invoke('process_invoice_2026_04_09', {
      body: {
        action: 'create',
        customer_name: invoice.customer_name,
        customer_email: invoice.customer_email,
        issue_date: invoice.issue_date,
        due_date: invoice.due_date,
        items: items,
        notes: invoice.notes
      }
    });
    if (error) throw error;
    return data.invoice;
  },

  async update(id: string, updates: Partial<Invoice>): Promise<Invoice> {
    const { data, error } = await supabase
      .from('invoices_2026_04_09')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('invoices_2026_04_09')
      .delete()
      .eq('id', id);
    if (error) throw error;
  },
};

// ============================================
// TRANSACTIONS SERVICE
// ============================================
export interface Transaction {
  id: string;
  transaction_type: 'income' | 'expense';
  category: string;
  description: string;
  amount: number;
  transaction_date: string;
  payment_method?: string;
  reference?: string;
  transaction_status: 'pending' | 'completed' | 'cancelled';
  created_at: string;
  updated_at: string;
}

export const transactionsService = {
  async getAll(): Promise<Transaction[]> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .select('*')
      .order('transaction_date', { ascending: false });
    if (error) throw error;
    return data || [];
  },

  async getByDateRange(startDate: string, endDate: string): Promise<Transaction[]> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .select('*')
      .gte('transaction_date', startDate)
      .lte('transaction_date', endDate)
      .order('transaction_date', { ascending: false });
    if (error) throw error;
    return data || [];
  },

  async create(transaction: Omit<Transaction, 'id' | 'created_at' | 'updated_at'>): Promise<Transaction> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .insert(transaction)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: Partial<Transaction>): Promise<Transaction> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('transactions_2026_04_09')
      .delete()
      .eq('id', id);
    if (error) throw error;
  },

  async getSummary(startDate: string, endDate: string) {
    const transactions = await this.getByDateRange(startDate, endDate);
    
    const income = transactions
      .filter((t: Transaction) => t.transaction_type === 'income')
      .reduce((sum: number, t: Transaction) => sum + parseFloat(t.amount.toString()), 0);
    
    const expenses = transactions
      .filter((t: Transaction) => t.transaction_type === 'expense')
      .reduce((sum: number, t: Transaction) => sum + parseFloat(t.amount.toString()), 0);

    return {
      income,
      expenses,
      balance: income - expenses,
      transaction_count: transactions.length
    };
  },
};

// ============================================
// EMPLOYEES SERVICE
// ============================================
export interface Employee {
  id: string;
  full_name: string;
  email: string;
  phone?: string;
  position: string;
  department: string;
  hire_date: string;
  salary: number;
  employee_status: 'active' | 'inactive' | 'suspended';
  created_at: string;
  updated_at: string;
}

export const employeesService = {
  async getAll(): Promise<Employee[]> {
    const { data, error } = await supabase
      .from('employees_2026_04_10')
      .select('*')
      .order('full_name', { ascending: true });
    if (error) throw error;
    return data || [];
  },

  async getById(id: string): Promise<Employee | null> {
    const { data, error } = await supabase
      .from('employees_2026_04_10')
      .select('*')
      .eq('id', id)
      .single();
    if (error) throw error;
    return data;
  },

  async create(employee: Omit<Employee, 'id' | 'created_at' | 'updated_at'>): Promise<Employee> {
    const { data, error } = await supabase
      .from('employees_2026_04_10')
      .insert(employee)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: Partial<Employee>): Promise<Employee> {
    const { data, error } = await supabase
      .from('employees_2026_04_10')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('employees_2026_04_10')
      .delete()
      .eq('id', id);
    if (error) throw error;
  },
};

// ============================================
// PROJECTS SERVICE
// ============================================
export interface Project {
  id: string;
  name: string;
  description?: string;
  client?: string;
  start_date: string;
  end_date?: string;
  budget: number;
  spent: number;
  progress: number;
  project_status: 'planning' | 'active' | 'paused' | 'completed' | 'cancelled';
  team_size: number;
  created_at: string;
  updated_at: string;
}

export const projectsService = {
  async getAll(): Promise<Project[]> {
    const { data, error } = await supabase
      .from('projects_2026_04_10')
      .select('*')
      .order('start_date', { ascending: false });
    if (error) throw error;
    return data || [];
  },

  async getById(id: string): Promise<Project | null> {
    const { data, error } = await supabase
      .from('projects_2026_04_10')
      .select('*')
      .eq('id', id)
      .single();
    if (error) throw error;
    return data;
  },

  async create(project: Omit<Project, 'id' | 'created_at' | 'updated_at'>): Promise<Project> {
    const { data, error } = await supabase
      .from('projects_2026_04_10')
      .insert(project)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: Partial<Project>): Promise<Project> {
    const { data, error } = await supabase
      .from('projects_2026_04_10')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('projects_2026_04_10')
      .delete()
      .eq('id', id);
    if (error) throw error;
  },
};

// ============================================
// TASKS SERVICE
// ============================================
export interface Task {
  id: string;
  title: string;
  description?: string;
  project_id?: string;
  assigned_to?: string;
  priority: 'low' | 'medium' | 'high';
  task_status: 'todo' | 'in_progress' | 'completed' | 'blocked';
  due_date?: string;
  created_at: string;
  updated_at: string;
}

export const tasksService = {
  async getAll(): Promise<Task[]> {
    const { data, error } = await supabase
      .from('tasks_2026_04_10')
      .select('*')
      .order('created_at', { ascending: false });
    if (error) throw error;
    return data || [];
  },

  async getById(id: string): Promise<Task | null> {
    const { data, error } = await supabase
      .from('tasks_2026_04_10')
      .select('*')
      .eq('id', id)
      .single();
    if (error) throw error;
    return data;
  },

  async getByProject(projectId: string): Promise<Task[]> {
    const { data, error } = await supabase
      .from('tasks_2026_04_10')
      .select('*')
      .eq('project_id', projectId)
      .order('created_at', { ascending: false });
    if (error) throw error;
    return data || [];
  },

  async create(task: Omit<Task, 'id' | 'created_at' | 'updated_at'>): Promise<Task> {
    const { data, error } = await supabase
      .from('tasks_2026_04_10')
      .insert(task)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: Partial<Task>): Promise<Task> {
    const { data, error } = await supabase
      .from('tasks_2026_04_10')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('tasks_2026_04_10')
      .delete()
      .eq('id', id);
    if (error) throw error;
  },
};
