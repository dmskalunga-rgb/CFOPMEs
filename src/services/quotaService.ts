// =====================================================
// KWANZACONTROL - Quota Management Service
// Gerencia limites e quotas baseados no plano de assinatura
// =====================================================

import { supabase } from '@/integrations/supabase/client';
import { getCurrentSubscription, type SubscriptionPlan } from './stripeService';

export interface QuotaCheck {
  allowed: boolean;
  used: number;
  limit: number;
  percentage: number;
  remaining: number;
  message?: string;
}

export interface QuotaLimits {
  users: number;
  invoices_per_month: number;
  storage_gb: number;
  employees: number;
  transactions_per_month: number;
  reports_per_month: number;
  api_calls_per_day: number;
}

// Obter limites do plano atual
export async function getCurrentLimits(): Promise<QuotaLimits> {
  const subscription = await getCurrentSubscription();
  
  if (!subscription?.subscription_plans) {
    // Limites padrão (free tier)
    return {
      users: 1,
      invoices_per_month: 10,
      storage_gb: 1,
      employees: 5,
      transactions_per_month: 50,
      reports_per_month: 5,
      api_calls_per_day: 100,
    };
  }

  const plan = subscription.subscription_plans;
  const limits = plan.limits as any;

  return {
    users: limits.users === -1 ? Infinity : limits.users,
    invoices_per_month: limits.invoices_per_month === -1 ? Infinity : limits.invoices_per_month,
    storage_gb: limits.storage_gb === -1 ? Infinity : limits.storage_gb,
    employees: limits.users === -1 ? Infinity : Math.max(limits.users * 2, 10),
    transactions_per_month: limits.invoices_per_month === -1 ? Infinity : limits.invoices_per_month * 5,
    reports_per_month: limits.invoices_per_month === -1 ? Infinity : Math.max(limits.invoices_per_month / 2, 10),
    api_calls_per_day: limits.users === -1 ? Infinity : limits.users * 1000,
  };
}

// Obter organização do usuário atual
async function getCurrentOrganizationId(): Promise<string | null> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  const { data: profile } = await supabase
    .from('user_profiles_iam')
    .select('organization_id')
    .eq('id', user.id)
    .single();

  return profile?.organization_id || null;
}

// Verificar quota de usuários
export async function checkUsersQuota(): Promise<QuotaCheck> {
  const orgId = await getCurrentOrganizationId();
  if (!orgId) {
    return { allowed: false, used: 0, limit: 0, percentage: 0, remaining: 0, message: 'Organização não encontrada' };
  }

  const limits = await getCurrentLimits();
  const limit = limits.users;

  // Contar usuários ativos
  const { count, error } = await supabase
    .from('user_profiles_iam')
    .select('*', { count: 'exact', head: true })
    .eq('organization_id', orgId)
    .eq('status', 'active');

  if (error) throw error;

  const used = count || 0;
  const percentage = limit === Infinity ? 0 : (used / limit) * 100;
  const remaining = limit === Infinity ? Infinity : Math.max(0, limit - used);
  const allowed = used < limit;

  return {
    allowed,
    used,
    limit,
    percentage,
    remaining,
    message: allowed ? undefined : `Limite de ${limit} usuários atingido. Faça upgrade do plano.`,
  };
}

// Verificar quota de faturas (este mês)
export async function checkInvoicesQuota(): Promise<QuotaCheck> {
  const orgId = await getCurrentOrganizationId();
  if (!orgId) {
    return { allowed: false, used: 0, limit: 0, percentage: 0, remaining: 0, message: 'Organização não encontrada' };
  }

  const limits = await getCurrentLimits();
  const limit = limits.invoices_per_month;

  // Contar faturas deste mês
  const startOfMonth = new Date();
  startOfMonth.setDate(1);
  startOfMonth.setHours(0, 0, 0, 0);

  const { count, error } = await supabase
    .from('invoices')
    .select('*', { count: 'exact', head: true })
    .eq('organization_id', orgId)
    .gte('created_at', startOfMonth.toISOString());

  if (error) throw error;

  const used = count || 0;
  const percentage = limit === Infinity ? 0 : (used / limit) * 100;
  const remaining = limit === Infinity ? Infinity : Math.max(0, limit - used);
  const allowed = used < limit;

  return {
    allowed,
    used,
    limit,
    percentage,
    remaining,
    message: allowed ? undefined : `Limite de ${limit} faturas/mês atingido. Faça upgrade do plano.`,
  };
}

// Verificar quota de funcionários
export async function checkEmployeesQuota(): Promise<QuotaCheck> {
  const orgId = await getCurrentOrganizationId();
  if (!orgId) {
    return { allowed: false, used: 0, limit: 0, percentage: 0, remaining: 0, message: 'Organização não encontrada' };
  }

  const limits = await getCurrentLimits();
  const limit = limits.employees;

  // Contar funcionários ativos
  const { count, error } = await supabase
    .from('employees')
    .select('*', { count: 'exact', head: true })
    .eq('organization_id', orgId)
    .eq('status', 'active');

  if (error) throw error;

  const used = count || 0;
  const percentage = limit === Infinity ? 0 : (used / limit) * 100;
  const remaining = limit === Infinity ? Infinity : Math.max(0, limit - used);
  const allowed = used < limit;

  return {
    allowed,
    used,
    limit,
    percentage,
    remaining,
    message: allowed ? undefined : `Limite de ${limit} funcionários atingido. Faça upgrade do plano.`,
  };
}

// Verificar quota de transações (este mês)
export async function checkTransactionsQuota(): Promise<QuotaCheck> {
  const orgId = await getCurrentOrganizationId();
  if (!orgId) {
    return { allowed: false, used: 0, limit: 0, percentage: 0, remaining: 0, message: 'Organização não encontrada' };
  }

  const limits = await getCurrentLimits();
  const limit = limits.transactions_per_month;

  // Contar transações deste mês
  const startOfMonth = new Date();
  startOfMonth.setDate(1);
  startOfMonth.setHours(0, 0, 0, 0);

  const { count, error } = await supabase
    .from('transactions')
    .select('*', { count: 'exact', head: true })
    .eq('organization_id', orgId)
    .gte('created_at', startOfMonth.toISOString());

  if (error) throw error;

  const used = count || 0;
  const percentage = limit === Infinity ? 0 : (used / limit) * 100;
  const remaining = limit === Infinity ? Infinity : Math.max(0, limit - used);
  const allowed = used < limit;

  return {
    allowed,
    used,
    limit,
    percentage,
    remaining,
    message: allowed ? undefined : `Limite de ${limit} transações/mês atingido. Faça upgrade do plano.`,
  };
}

// Verificar quota de relatórios (este mês)
export async function checkReportsQuota(): Promise<QuotaCheck> {
  const orgId = await getCurrentOrganizationId();
  if (!orgId) {
    return { allowed: false, used: 0, limit: 0, percentage: 0, remaining: 0, message: 'Organização não encontrada' };
  }

  const limits = await getCurrentLimits();
  const limit = limits.reports_per_month;

  // Contar relatórios deste mês
  const startOfMonth = new Date();
  startOfMonth.setDate(1);
  startOfMonth.setHours(0, 0, 0, 0);

  const { count, error } = await supabase
    .from('reports')
    .select('*', { count: 'exact', head: true })
    .eq('organization_id', orgId)
    .gte('created_at', startOfMonth.toISOString());

  if (error) throw error;

  const used = count || 0;
  const percentage = limit === Infinity ? 0 : (used / limit) * 100;
  const remaining = limit === Infinity ? Infinity : Math.max(0, limit - used);
  const allowed = used < limit;

  return {
    allowed,
    used,
    limit,
    percentage,
    remaining,
    message: allowed ? undefined : `Limite de ${limit} relatórios/mês atingido. Faça upgrade do plano.`,
  };
}

// Verificar quota de armazenamento
export async function checkStorageQuota(): Promise<QuotaCheck> {
  const orgId = await getCurrentOrganizationId();
  if (!orgId) {
    return { allowed: false, used: 0, limit: 0, percentage: 0, remaining: 0, message: 'Organização não encontrada' };
  }

  const limits = await getCurrentLimits();
  const limit = limits.storage_gb;

  // TODO: Implementar cálculo real de storage
  // Por enquanto, retornar valores mock
  const used = 0.5; // GB
  const percentage = limit === Infinity ? 0 : (used / limit) * 100;
  const remaining = limit === Infinity ? Infinity : Math.max(0, limit - used);
  const allowed = used < limit;

  return {
    allowed,
    used,
    limit,
    percentage,
    remaining,
    message: allowed ? undefined : `Limite de ${limit}GB de armazenamento atingido. Faça upgrade do plano.`,
  };
}

// Verificar todas as quotas
export async function checkAllQuotas(): Promise<{
  users: QuotaCheck;
  invoices: QuotaCheck;
  employees: QuotaCheck;
  transactions: QuotaCheck;
  reports: QuotaCheck;
  storage: QuotaCheck;
}> {
  const [users, invoices, employees, transactions, reports, storage] = await Promise.all([
    checkUsersQuota(),
    checkInvoicesQuota(),
    checkEmployeesQuota(),
    checkTransactionsQuota(),
    checkReportsQuota(),
    checkStorageQuota(),
  ]);

  return {
    users,
    invoices,
    employees,
    transactions,
    reports,
    storage,
  };
}

// Verificar se pode executar ação
export async function canPerformAction(action: 'create_user' | 'create_invoice' | 'create_employee' | 'create_transaction' | 'create_report'): Promise<{
  allowed: boolean;
  message?: string;
}> {
  let quota: QuotaCheck;

  switch (action) {
    case 'create_user':
      quota = await checkUsersQuota();
      break;
    case 'create_invoice':
      quota = await checkInvoicesQuota();
      break;
    case 'create_employee':
      quota = await checkEmployeesQuota();
      break;
    case 'create_transaction':
      quota = await checkTransactionsQuota();
      break;
    case 'create_report':
      quota = await checkReportsQuota();
      break;
    default:
      return { allowed: true };
  }

  return {
    allowed: quota.allowed,
    message: quota.message,
  };
}
