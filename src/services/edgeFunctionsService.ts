/**
 * KwanzaControl - Edge Functions Service
 * Serviço centralizado para chamar as Edge Functions deployadas no Supabase
 */
import { supabase } from '@/integrations/supabase/client';

// Helper para invocar Edge Functions com tratamento de erros
async function invokeFunction<T = unknown>(
  functionName: string,
  body?: Record<string, unknown>,
  method: string = 'POST'
): Promise<T> {
  const { data, error } = await supabase.functions.invoke(functionName, {
    body,
    method: method as 'GET' | 'POST' | 'PUT' | 'DELETE'
  });
  if (error) throw new Error(error.message || `Erro na função ${functionName}`);
  return data as T;
}

// =====================================================
// AUTH & USER
// =====================================================
export const authEdgeService = {
  async register(payload: {
    email: string;
    password: string;
    fullName: string;
    companyName: string;
    nif?: string;
    phone?: string;
  }) {
    return invokeFunction('auth-register', payload);
  },

  async getProfile() {
    return invokeFunction('user-profile', undefined, 'GET');
  },

  async updateProfile(updates: Record<string, unknown>) {
    return invokeFunction('user-profile', updates, 'PUT');
  }
};

// =====================================================
// FATURAS
// =====================================================
export const invoicesEdgeService = {
  async list(params?: {
    page?: number;
    limit?: number;
    status?: string;
    search?: string;
    from_date?: string;
    to_date?: string;
  }) {
    return invokeFunction('invoices-crud', params, 'GET');
  },

  async get(id: string) {
    return invokeFunction('invoices-crud', { id }, 'GET');
  },

  async create(invoice: Record<string, unknown>, items: Record<string, unknown>[]) {
    return invokeFunction('invoices-crud', { invoice, items });
  },

  async update(id: string, updates: Record<string, unknown>) {
    return invokeFunction('invoices-crud', { id, ...updates }, 'PUT');
  },

  async delete(id: string) {
    return invokeFunction('invoices-crud', { id }, 'DELETE');
  },

  async submitToAGT(invoiceId: string, action: 'validate' | 'cancel' = 'validate') {
    return invokeFunction('agt-invoice-submit', { invoiceId, action });
  },

  async generatePDF(invoiceId: string) {
    return invokeFunction('invoice-pdf-generate', { invoiceId });
  }
};

// =====================================================
// CLIENTES
// =====================================================
export const customersEdgeService = {
  async list(params?: {
    page?: number;
    limit?: number;
    search?: string;
    active?: boolean;
  }) {
    return invokeFunction('customers-crud', params, 'GET');
  },

  async get(id: string) {
    return invokeFunction('customers-crud', { id }, 'GET');
  },

  async create(customer: Record<string, unknown>) {
    return invokeFunction('customers-crud', customer);
  },

  async update(id: string, updates: Record<string, unknown>) {
    return invokeFunction('customers-crud', { id, ...updates }, 'PUT');
  },

  async delete(id: string) {
    return invokeFunction('customers-crud', { id }, 'DELETE');
  }
};

// =====================================================
// FUNCIONÁRIOS
// =====================================================
export const employeesEdgeService = {
  async list(params?: {
    page?: number;
    limit?: number;
    search?: string;
    status?: string;
    department?: string;
  }) {
    return invokeFunction('employees-crud', params, 'GET');
  },

  async get(id: string) {
    return invokeFunction('employees-crud', { id }, 'GET');
  },

  async listDepartments() {
    return invokeFunction('employees-crud', { list: 'departments' }, 'GET');
  },

  async create(employee: Record<string, unknown>) {
    return invokeFunction('employees-crud', employee);
  },

  async update(id: string, updates: Record<string, unknown>) {
    return invokeFunction('employees-crud', { id, ...updates }, 'PUT');
  },

  async terminate(id: string) {
    return invokeFunction('employees-crud', { id }, 'DELETE');
  }
};

// =====================================================
// FOLHA DE PAGAMENTOS
// =====================================================
export const payrollEdgeService = {
  async list(params?: { month?: string; employee_id?: string }) {
    return invokeFunction('payroll-process', params, 'GET');
  },

  async processBulk(payroll_month: string, employee_ids?: string[]) {
    return invokeFunction('payroll-process', {
      action: 'process_bulk',
      payroll_month,
      employee_ids
    });
  },

  async createSingle(payslip: Record<string, unknown>) {
    return invokeFunction('payroll-process', { action: 'create_single', payslip });
  },

  async markPaid(payslip_ids: string[], payment_date: string, payment_reference?: string) {
    return invokeFunction('payroll-process', {
      action: 'mark_paid',
      payslip_ids,
      payment_date,
      payment_reference
    });
  },

  async generatePDF(payslipId: string) {
    return invokeFunction('payslip-pdf-generate', { payslipId });
  }
};

// =====================================================
// TRANSAÇÕES FINANCEIRAS
// =====================================================
export const transactionsEdgeService = {
  async list(params?: {
    page?: number;
    limit?: number;
    type?: string;
    search?: string;
    from_date?: string;
    to_date?: string;
    category_id?: string;
  }) {
    return invokeFunction('transactions-crud', params, 'GET');
  },

  async get(id: string) {
    return invokeFunction('transactions-crud', { id }, 'GET');
  },

  async create(transaction: Record<string, unknown>) {
    return invokeFunction('transactions-crud', transaction);
  },

  async bulkImport(transactions: Record<string, unknown>[]) {
    return invokeFunction('transactions-crud', { action: 'bulk_import', transactions });
  },

  async reconcile(transaction_ids: string[]) {
    return invokeFunction('transactions-crud', { action: 'reconcile', transaction_ids });
  },

  async update(id: string, updates: Record<string, unknown>) {
    return invokeFunction('transactions-crud', { id, ...updates }, 'PUT');
  },

  async delete(id: string) {
    return invokeFunction('transactions-crud', { id }, 'DELETE');
  }
};

// =====================================================
// DASHBOARD ANALYTICS
// =====================================================
export const dashboardEdgeService = {
  async getAll(period: 'month' | 'quarter' | 'year' = 'month') {
    return invokeFunction('dashboard-analytics', { section: 'all', period }, 'GET');
  },

  async getKPIs(period: 'month' | 'quarter' | 'year' = 'month') {
    return invokeFunction('dashboard-analytics', { section: 'kpis', period }, 'GET');
  },

  async getCashflow() {
    return invokeFunction('dashboard-analytics', { section: 'cashflow' }, 'GET');
  },

  async getTopCustomers() {
    return invokeFunction('dashboard-analytics', { section: 'top_customers' }, 'GET');
  },

  async getNotifications() {
    return invokeFunction('dashboard-analytics', { section: 'notifications' }, 'GET');
  }
};

// =====================================================
// RELATÓRIOS
// =====================================================
export const reportsEdgeService = {
  async list() {
    return invokeFunction('reports-generate', { type: 'list' }, 'GET');
  },

  async getDashboard() {
    return invokeFunction('reports-generate', { type: 'dashboard' }, 'GET');
  },

  async generate(type: string, parameters?: Record<string, unknown>) {
    return invokeFunction('reports-generate', { type, parameters });
  },

  async generateIncomeStatement(from_date: string, to_date: string) {
    return invokeFunction('reports-generate', {
      type: 'INCOME_STATEMENT', parameters: { from_date, to_date }
    });
  },

  async generatePayrollSummary(month: string) {
    return invokeFunction('reports-generate', {
      type: 'PAYROLL_SUMMARY', parameters: { month }
    });
  },

  async generateInvoiceReport(from_date: string, to_date: string) {
    return invokeFunction('reports-generate', {
      type: 'INVOICE_REPORT', parameters: { from_date, to_date }
    });
  },

  async generateAGTReport(from_date: string, to_date: string) {
    return invokeFunction('reports-generate', {
      type: 'AGT_REPORT', parameters: { from_date, to_date }
    });
  }
};

// =====================================================
// PRODUTOS & INVENTÁRIO
// =====================================================
export const inventoryEdgeService = {
  async listProducts(params?: {
    page?: number;
    limit?: number;
    search?: string;
    category?: string;
    low_stock?: boolean;
  }) {
    return invokeFunction('products-inventory', { resource: 'products', ...params }, 'GET');
  },

  async getProduct(id: string) {
    return invokeFunction('products-inventory', { resource: 'products', id }, 'GET');
  },

  async createProduct(product: Record<string, unknown>) {
    return invokeFunction('products-inventory', { resource: 'products', ...product });
  },

  async updateProduct(id: string, updates: Record<string, unknown>) {
    return invokeFunction('products-inventory', { resource: 'products', id, ...updates }, 'PUT');
  },

  async deleteProduct(id: string) {
    return invokeFunction('products-inventory', { resource: 'products', id }, 'DELETE');
  },

  async listMovements(product_id?: string) {
    return invokeFunction('products-inventory', { resource: 'movements', product_id }, 'GET');
  },

  async createMovement(movement: Record<string, unknown>) {
    return invokeFunction('products-inventory', { resource: 'movements', ...movement });
  },

  async listCategories() {
    return invokeFunction('products-inventory', { resource: 'categories' }, 'GET');
  },

  async getLowStockAlerts() {
    return invokeFunction('products-inventory', { resource: 'alerts' }, 'GET');
  }
};

// =====================================================
// EMAIL
// =====================================================
export const emailEdgeService = {
  async sendInvoice(to: string, invoice: Record<string, unknown>, tenant: Record<string, unknown>, customerName: string) {
    return invokeFunction('email-send', { type: 'invoice', data: { to, invoice, tenant, customerName } });
  },

  async sendPayslip(to: string, payslip: Record<string, unknown>, employee: Record<string, unknown>, tenant: Record<string, unknown>) {
    return invokeFunction('email-send', { type: 'payslip', data: { to, payslip, employee, tenant } });
  },

  async sendNotification(to: string, title: string, message: string, action_url?: string) {
    return invokeFunction('email-send', { type: 'notification', data: { to, title, message, action_url } });
  },

  async sendWelcome(to: string, userName: string, companyName: string) {
    return invokeFunction('email-send', { type: 'welcome', data: { to, userName, companyName } });
  }
};

// =====================================================
// AUDITORIA & NOTIFICAÇÕES
// =====================================================
export const auditEdgeService = {
  async getLogs(params?: {
    page?: number;
    limit?: number;
    action?: string;
    user_id?: string;
    resource_type?: string;
    from_date?: string;
    to_date?: string;
  }) {
    return invokeFunction('audit-notifications', { resource: 'logs', ...params }, 'GET');
  },

  async logEvent(event: {
    action: string;
    resource_type: string;
    resource_id?: string;
    old_values?: Record<string, unknown>;
    new_values?: Record<string, unknown>;
  }) {
    return invokeFunction('audit-notifications', { resource: 'logs', ...event });
  },

  async getNotifications(unread?: boolean) {
    return invokeFunction('audit-notifications', { resource: 'notifications', unread }, 'GET');
  },

  async markNotificationsRead(ids?: string[], mark_all?: boolean) {
    return invokeFunction('audit-notifications', { resource: 'notifications', ids, mark_all }, 'PUT');
  },

  async sendNotification(user_ids: string[], title: string, message: string, type?: string, action_url?: string) {
    return invokeFunction('audit-notifications', { resource: 'notifications', user_ids, title, message, type, action_url });
  }
};

// =====================================================
// IAM - CONTROLO DE ACESSOS
// =====================================================
export const iamEdgeService = {
  async listUsers() {
    return invokeFunction('iam-access-control', { resource: 'users' }, 'GET');
  },

  async createUser(userData: { email: string; full_name: string; role: string; password?: string }) {
    return invokeFunction('iam-access-control', { resource: 'users', ...userData });
  },

  async updateUser(id: string, updates: { role?: string; status?: string }) {
    return invokeFunction('iam-access-control', { resource: 'users', id, ...updates }, 'PUT');
  },

  async deactivateUser(id: string) {
    return invokeFunction('iam-access-control', { resource: 'users', id }, 'DELETE');
  },

  async listRoles() {
    return invokeFunction('iam-access-control', { resource: 'roles' }, 'GET');
  },

  async createRole(role: Record<string, unknown>) {
    return invokeFunction('iam-access-control', { resource: 'roles', ...role });
  },

  async listPermissions() {
    return invokeFunction('iam-access-control', { resource: 'permissions' }, 'GET');
  },

  async getMyPermissions() {
    return invokeFunction('iam-access-control', { resource: 'my_permissions' }, 'GET');
  }
};

// =====================================================
// CONFIGURAÇÕES DO TENANT
// =====================================================
export const tenantEdgeService = {
  async get() {
    return invokeFunction('tenant-settings', undefined, 'GET');
  },

  async update(tenant_data?: Record<string, unknown>, settings_data?: Record<string, unknown>) {
    return invokeFunction('tenant-settings', { tenant_data, settings_data }, 'PUT');
  },

  async getUploadLogoUrl(file_name: string) {
    return invokeFunction('tenant-settings', { action: 'upload_logo', file_name });
  },

  async testAGTConnection() {
    return invokeFunction('tenant-settings', { action: 'test_agt' });
  }
};

// =====================================================
// INSIGHTS DE IA
// =====================================================
export const aiInsightsEdgeService = {
  async list() {
    return invokeFunction('ai-insights', { type: 'list' }, 'GET');
  },

  async generate(type?: string) {
    return invokeFunction('ai-insights', { type });
  }
};

// =====================================================
// EXPORTAÇÃO DE DADOS
// =====================================================
export const dataExportEdgeService = {
  async exportInvoices(filters?: { from_date?: string; to_date?: string; status?: string }) {
    return invokeFunction('data-export', { type: 'invoices', filters });
  },

  async exportCustomers() {
    return invokeFunction('data-export', { type: 'customers' });
  },

  async exportEmployees() {
    return invokeFunction('data-export', { type: 'employees' });
  },

  async exportTransactions(filters?: { from_date?: string; to_date?: string; type?: string }) {
    return invokeFunction('data-export', { type: 'transactions', filters });
  },

  async exportPayroll(month?: string) {
    return invokeFunction('data-export', { type: 'payroll', filters: { month } });
  },

  // Descarregar CSV no browser
  downloadCSV(content: string, filename: string) {
    const blob = new Blob(['\uFEFF' + content], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }
};

// =====================================================
// DOCUMENTOS
// =====================================================
export const documentsEdgeService = {
  async list(params?: { category?: string; search?: string }) {
    return invokeFunction('documents-manage', params, 'GET');
  },

  async getDownloadUrl(id: string) {
    return invokeFunction('documents-manage', { action: 'download', id }, 'GET');
  },

  async getUploadUrl(file_name: string) {
    return invokeFunction('documents-manage', { action: 'upload_url', file_name }, 'GET');
  },

  async create(document: Record<string, unknown>) {
    return invokeFunction('documents-manage', document);
  },

  async update(id: string, updates: Record<string, unknown>) {
    return invokeFunction('documents-manage', { id, ...updates }, 'PUT');
  },

  async delete(id: string) {
    return invokeFunction('documents-manage', { id }, 'DELETE');
  }
};

// =====================================================
// WEBHOOKS & INTEGRAÇÕES
// =====================================================
export const webhooksEdgeService = {
  async listWebhooks() {
    return invokeFunction('webhooks-integrations', { resource: 'webhooks' }, 'GET');
  },

  async createWebhook(webhook: Record<string, unknown>) {
    return invokeFunction('webhooks-integrations', { resource: 'webhooks', ...webhook });
  },

  async deleteWebhook(id: string) {
    return invokeFunction('webhooks-integrations', { resource: 'webhooks', id }, 'DELETE');
  },

  async listIntegrations() {
    return invokeFunction('webhooks-integrations', { resource: 'integrations' }, 'GET');
  },

  async saveIntegration(integration: Record<string, unknown>) {
    return invokeFunction('webhooks-integrations', { resource: 'integrations', ...integration });
  }
};
