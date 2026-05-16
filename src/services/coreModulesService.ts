// Core Modules Service - Serviço unificado para todos os módulos principais
import { supabase } from '@/integrations/supabase/client';

const EDGE_FUNCTION = 'core_modules_manager_2026_04_08';

// Types
export interface Customer {
  id: string;
  name: string;
  email?: string;
  phone?: string;
  tax_id?: string;
  address?: string;
  city?: string;
  country?: string;
  is_active: boolean;
  created_at: string;
}

export interface Product {
  id: string;
  name: string;
  description?: string;
  sku?: string;
  price: number;
  cost?: number;
  tax_rate: number;
  unit: string;
  category?: string;
  is_active: boolean;
  stock_quantity: number;
}

export interface Invoice {
  id: string;
  customer_id: string;
  invoice_number: string;
  issue_date: string;
  due_date: string;
  status: string;
  subtotal: number;
  tax_amount: number;
  discount_amount: number;
  total_amount: number;
  currency: string;
  notes?: string;
  created_at: string;
  customers_2026_04_08?: {
    id: string;
    name: string;
    email?: string;
  };
}

export interface Employee {
  id: string;
  employee_number: string;
  first_name: string;
  last_name: string;
  email?: string;
  phone?: string;
  position?: string;
  department?: string;
  hire_date: string;
  salary: number;
  is_active: boolean;
}

export interface DashboardStats {
  total_customers: number;
  total_products: number;
  total_invoices: number;
  total_revenue: number;
  pending_invoices: number;
  total_employees: number;
  monthly_expenses: number;
}

class CoreModulesService {
  private async callEdgeFunction(action: string, module: string, data?: any, id?: string) {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) throw new Error('User not authenticated');

    const { data: result, error } = await supabase.functions.invoke(EDGE_FUNCTION, {
      body: { action, module, data, userId: user.id, id }
    });

    if (error) throw error;
    if (!result.success) throw new Error(result.error || 'Operation failed');
    return result.data;
  }

  // ===== CUSTOMERS =====
  async getCustomers(): Promise<Customer[]> {
    return this.callEdgeFunction('list', 'customers');
  }

  async createCustomer(customer: Partial<Customer>): Promise<Customer> {
    return this.callEdgeFunction('create', 'customers', customer);
  }

  async updateCustomer(id: string, customer: Partial<Customer>): Promise<Customer> {
    return this.callEdgeFunction('update', 'customers', customer, id);
  }

  async deleteCustomer(id: string): Promise<void> {
    await this.callEdgeFunction('delete', 'customers', undefined, id);
  }

  // ===== PRODUCTS =====
  async getProducts(): Promise<Product[]> {
    return this.callEdgeFunction('list', 'products');
  }

  async createProduct(product: Partial<Product>): Promise<Product> {
    return this.callEdgeFunction('create', 'products', product);
  }

  async updateProduct(id: string, product: Partial<Product>): Promise<Product> {
    return this.callEdgeFunction('update', 'products', product, id);
  }

  async deleteProduct(id: string): Promise<void> {
    await this.callEdgeFunction('delete', 'products', undefined, id);
  }

  // ===== INVOICES =====
  async getInvoices(): Promise<Invoice[]> {
    return this.callEdgeFunction('list', 'invoices');
  }

  async createInvoice(invoice: Partial<Invoice>): Promise<Invoice> {
    return this.callEdgeFunction('create', 'invoices', invoice);
  }

  async updateInvoice(id: string, invoice: Partial<Invoice>): Promise<Invoice> {
    return this.callEdgeFunction('update', 'invoices', invoice, id);
  }

  async deleteInvoice(id: string): Promise<void> {
    await this.callEdgeFunction('delete', 'invoices', undefined, id);
  }

  // ===== EMPLOYEES =====
  async getEmployees(): Promise<Employee[]> {
    return this.callEdgeFunction('list', 'employees');
  }

  async createEmployee(employee: Partial<Employee>): Promise<Employee> {
    return this.callEdgeFunction('create', 'employees', employee);
  }

  async updateEmployee(id: string, employee: Partial<Employee>): Promise<Employee> {
    return this.callEdgeFunction('update', 'employees', employee, id);
  }

  async deleteEmployee(id: string): Promise<void> {
    await this.callEdgeFunction('delete', 'employees', undefined, id);
  }

  // ===== DASHBOARD STATS =====
  async getDashboardStats(): Promise<DashboardStats> {
    const stats = await this.callEdgeFunction('dashboard_stats', '');
    return stats || {
      total_customers: 0,
      total_products: 0,
      total_invoices: 0,
      total_revenue: 0,
      pending_invoices: 0,
      total_employees: 0,
      monthly_expenses: 0,
    };
  }
}

export const coreModulesService = new CoreModulesService();
