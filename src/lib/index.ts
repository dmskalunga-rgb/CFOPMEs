export const ROUTE_PATHS = {
  HOME: '/',
  LOGIN: '/login',
  REGISTER: '/register',
  RESET_PASSWORD: '/reset-password',
  ONBOARDING: '/onboarding',
  BILLING: '/billing',
  DASHBOARD: '/dashboard',
  INVOICING: '/invoicing',
  PAYROLL: '/payroll',
  HR_MANAGEMENT: '/hr-management',
  FINANCE: '/finance',
  REPORTS: '/reports',
  SETTINGS: '/settings',
  // IAM & PAM Modules
  USERS: '/users',
  ROLES: '/roles',
  APPROVALS: '/approvals',
  AUDIT: '/audit',
  NOTIFICATIONS: '/notifications',
  METRICS: '/metrics',
  ADVANCED_SECURITY: '/advanced-security',
  ADVANCED_FINANCE: '/advanced-finance',
  BUSINESS_MANAGEMENT: '/business-management',
  DEVELOPERS: '/developers',
  MARKETPLACE: '/marketplace',
  FINANCIAL_PLANNING: '/financial-planning',
  MOBILE: '/mobile',
  // AI Modules
  AI_DASHBOARD: '/ai-dashboard',
  AI_UEBA: '/ai-ueba',
  AI_REPORTS: '/ai-reports',
  AI_DECISIONS: '/ai-decisions',
  // Advanced Modules
  ADVANCED_INVOICING: '/advanced-invoicing',
  ADVANCED_HR: '/advanced-hr',
  CONTEXT_ENGINE: '/context-engine',
  ADVANCED_FEATURES: '/advanced-features',
  AI_TRANSVERSAL: '/ai-transversal',
  // IAM + PAM + BILLING
  IAM_DASHBOARD: '/iam-dashboard',
  PAM_DASHBOARD: '/pam-dashboard',
  BILLING_DASHBOARD: '/billing-dashboard',
  RBAC_DASHBOARD: '/rbac-dashboard',
  INTEGRATION_STATUS: '/integration-status',
  SMART_COMPANY: '/smart-company',
  // New Features
  QA_DASHBOARD: '/qa-dashboard',
  RPA_DASHBOARD: '/rpa-dashboard',
  AI_CHAT: '/ai-chat',
  // Complete Modules
  MARKETPLACE_COMPLETE: '/marketplace-complete',
  METRICS_COMPLETE: '/metrics-complete',
  AUDIT_COMPLETE: '/audit-complete',
  // Commercial Plans
  COMMERCIAL_PLANS: '/commercial-plans',
  // Roadmap
  ROADMAP: '/roadmap',
  // AGT & Reports
  AGT_INTEGRATION: '/agt-integration',
  ADVANCED_REPORTS: '/advanced-reports',
  REPORT_VIEWER: '/report-viewer/:reportId',
  PERFORMANCE_MONITOR: '/performance-monitor',
  UX_SHOWCASE: '/ux-showcase',
  ANIMATIONS: '/animations',
  NOTIFICATIONS_MANAGEMENT: '/notifications-management',
  EXTERNAL_INTEGRATIONS: '/external-integrations',
  SECURITY_ANALYTICS: '/security-analytics',
  CORE_MODULES: '/core-modules',
} as const;

export enum UserRole {
  OWNER = 'OWNER',
  ADMIN = 'ADMIN',
  ACCOUNTANT = 'ACCOUNTANT',
  OPERATOR = 'OPERATOR',
  VIEWER = 'VIEWER',
}

export enum InvoiceStatus {
  DRAFT = 'DRAFT',
  SENT_AGT = 'SENT_AGT',
  VALIDATED = 'VALIDATED',
  REJECTED = 'REJECTED',
  PAID = 'PAID',
  CANCELLED = 'CANCELLED',
}

export enum TransactionType {
  INCOME = 'INCOME',
  EXPENSE = 'EXPENSE',
}

export interface User {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  companyId: string;
  avatar?: string;
  createdAt: Date;
}

export interface Company {
  id: string;
  name: string;
  nif: string;
  address: string;
  phone: string;
  email: string;
  logo?: string;
  agtCertificate?: string;
  agtPrivateKey?: string;
  createdAt: Date;
}

export interface InvoiceItem {
  id: string;
  description: string;
  quantity: number;
  unitPrice: number;
  taxRate: number;
  total: number;
}

export interface Invoice {
  id: string;
  number: string;
  companyId: string;
  clientName: string;
  clientNif: string;
  clientAddress: string;
  date: Date;
  dueDate: Date;
  items: InvoiceItem[];
  subtotal: number;
  taxAmount: number;
  total: number;
  status: InvoiceStatus;
  agtHash?: string;
  agtValidationDate?: Date;
  agtRejectionReason?: string;
  notes?: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface Transaction {
  id: string;
  companyId: string;
  type: TransactionType;
  category: string;
  description: string;
  amount: number;
  date: Date;
  reference?: string;
  invoiceId?: string;
  payrollId?: string;
  createdAt: Date;
}

export interface Employee {
  id: string;
  companyId: string;
  name: string;
  nif: string;
  email: string;
  phone: string;
  position: string;
  department: string;
  baseSalary: number;
  startDate: Date;
  avatar?: string;
  isActive: boolean;
  createdAt: Date;
}

export interface Payslip {
  id: string;
  companyId: string;
  employeeId: string;
  employeeName: string;
  month: string;
  year: number;
  baseSalary: number;
  allowances: number;
  deductions: number;
  inssEmployee: number;
  inssEmployer: number;
  irt: number;
  netSalary: number;
  generatedAt: Date;
  paidAt?: Date;
}

export interface DashboardKPI {
  title: string;
  value: string;
  change: number;
  trend: number[];
}

export interface Alert {
  id: string;
  type: 'info' | 'warning' | 'error' | 'success';
  title: string;
  message: string;
  date: Date;
  read: boolean;
}

export const formatCurrency = (amount: number): string => {
  return new Intl.NumberFormat('pt-AO', {
    style: 'currency',
    currency: 'AOA',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
};

export const formatNIF = (nif: string): string => {
  const cleaned = nif.replace(/\D/g, '');
  if (cleaned.length !== 9) return nif;
  return `${cleaned.slice(0, 3)}.${cleaned.slice(3, 6)}.${cleaned.slice(6)}`;
};

export const formatDate = (date: Date | string, format: 'short' | 'long' | 'full' = 'short'): string => {
  const d = typeof date === 'string' ? new Date(date) : date;
  
  switch (format) {
    case 'short':
      return new Intl.DateTimeFormat('pt-AO', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
      }).format(d);
    case 'long':
      return new Intl.DateTimeFormat('pt-AO', {
        day: '2-digit',
        month: 'long',
        year: 'numeric',
      }).format(d);
    case 'full':
      return new Intl.DateTimeFormat('pt-AO', {
        weekday: 'long',
        day: '2-digit',
        month: 'long',
        year: 'numeric',
      }).format(d);
    default:
      return d.toLocaleDateString('pt-AO');
  }
};

export const APP_CONFIG = {
  name: 'KWANZACONTROL',
  version: '1.0.0',
  year: 2026,
  currency: 'AOA',
  locale: 'pt-AO',
  timezone: 'Africa/Luanda',
  taxRates: {
    iva: {
      normal: 0.14,
      reduced: 0.05,
      exempt: 0,
    },
    inss: {
      employee: 0.03,
      employer: 0.08,
    },
  },
  agt: {
    apiUrl: 'https://agt.minfin.gov.ao/api',
    timeout: 30000,
  },
} as const;

export const INVOICE_SERIES_PREFIX = 'FT';
export const RECEIPT_SERIES_PREFIX = 'RC';
export const CREDIT_NOTE_PREFIX = 'NC';

export const TRANSACTION_CATEGORIES = {
  INCOME: [
    'Vendas',
    'Serviços',
    'Juros',
    'Dividendos',
    'Outros Rendimentos',
  ],
  EXPENSE: [
    'Salários',
    'Fornecedores',
    'Renda',
    'Utilidades',
    'Marketing',
    'Impostos',
    'Seguros',
    'Manutenção',
    'Viagens',
    'Formação',
    'Outros Custos',
  ],
} as const;

export const DEPARTMENTS = [
  'Administração',
  'Financeiro',
  'Recursos Humanos',
  'Comercial',
  'Marketing',
  'Operações',
  'TI',
  'Logística',
] as const;

export const POSITIONS = [
  'Diretor Geral',
  'Diretor Financeiro',
  'Gestor',
  'Supervisor',
  'Técnico',
  'Assistente',
  'Operador',
] as const;