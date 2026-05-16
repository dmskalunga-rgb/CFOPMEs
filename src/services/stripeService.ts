// =====================================================
// KWANZACONTROL - Stripe Service
// Gerencia assinaturas e pagamentos via Stripe
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export interface SubscriptionPlan {
  id: string;
  name: string;
  slug: string;
  description: string;
  price_monthly: number;
  price_yearly: number;
  stripe_price_id: string | null;
  limits: {
    users: number;
    invoices_per_month: number;
    storage_gb: number;
  };
  features: string[];
  is_active: boolean;
}

export interface Subscription {
  id: string;
  organization_id: string;
  plan_id: string;
  stripe_subscription_id: string;
  stripe_customer_id: string;
  status: string;
  billing_interval: 'monthly' | 'yearly';
  current_period_start: string;
  current_period_end: string;
  trial_end: string | null;
  cancel_at_period_end: boolean;
  canceled_at: string | null;
  subscription_plans?: SubscriptionPlan;
}

export interface Invoice {
  id: string;
  organization_id: string;
  stripe_invoice_id: string;
  amount: number;
  currency: string;
  status: string;
  paid_at: string | null;
  invoice_pdf: string | null;
}

// Obter todos os planos disponíveis
export async function getPlans(): Promise<SubscriptionPlan[]> {
  const { data, error } = await supabase
    .from('subscription_plans')
    .select('*')
    .eq('is_active', true)
    .order('price_monthly', { ascending: true });

  if (error) throw error;
  return data || [];
}

// Obter assinatura atual da organização
export async function getCurrentSubscription(): Promise<Subscription | null> {
  const { data: profile } = await supabase
    .from('user_profiles_iam')
    .select('organization_id')
    .eq('id', (await supabase.auth.getUser()).data.user?.id)
    .single();

  if (!profile?.organization_id) return null;

  const { data, error } = await supabase
    .from('billing_subscriptions')
    .select('*, subscription_plans(*)')
    .eq('organization_id', profile.organization_id)
    .in('status', ['active', 'trialing', 'past_due'])
    .order('created_at', { ascending: false })
    .limit(1)
    .single();

  if (error && error.code !== 'PGRST116') throw error;
  return data || null;
}

// Criar sessão de checkout
export async function createCheckoutSession(
  planSlug: string,
  billingInterval: 'monthly' | 'yearly'
): Promise<{ sessionId: string; url: string; mock?: boolean }> {
  const { data, error } = await supabase.functions.invoke('create_checkout_session_2026_04_06', {
    body: { planSlug, billingInterval },
  });

  if (error) throw error;
  if (!data.success) throw new Error(data.error || 'Failed to create checkout session');

  return {
    sessionId: data.sessionId,
    url: data.url,
    mock: data.mock,
  };
}

// Cancelar assinatura (no final do período)
export async function cancelSubscription(): Promise<void> {
  const { data, error } = await supabase.functions.invoke('manage_subscription_2026_04_06', {
    body: { action: 'cancel' },
  });

  if (error) throw error;
  if (!data.success) throw new Error(data.error || 'Failed to cancel subscription');
}

// Reativar assinatura
export async function reactivateSubscription(): Promise<void> {
  const { data, error } = await supabase.functions.invoke('manage_subscription_2026_04_06', {
    body: { action: 'reactivate' },
  });

  if (error) throw error;
  if (!data.success) throw new Error(data.error || 'Failed to reactivate subscription');
}

// Mudar plano
export async function changePlan(planSlug: string): Promise<void> {
  const { data, error } = await supabase.functions.invoke('manage_subscription_2026_04_06', {
    body: { action: 'change_plan', planSlug },
  });

  if (error) throw error;
  if (!data.success) throw new Error(data.error || 'Failed to change plan');
}

// Obter histórico de faturas
export async function getInvoices(): Promise<Invoice[]> {
  const { data: profile } = await supabase
    .from('user_profiles_iam')
    .select('organization_id')
    .eq('id', (await supabase.auth.getUser()).data.user?.id)
    .single();

  if (!profile?.organization_id) return [];

  const { data, error } = await supabase
    .from('billing_invoices')
    .select('*')
    .eq('organization_id', profile.organization_id)
    .order('paid_at', { ascending: false });

  if (error) throw error;
  return data || [];
}

// Verificar se está em trial
export async function isInTrial(): Promise<boolean> {
  const subscription = await getCurrentSubscription();
  if (!subscription) return false;

  if (subscription.status === 'trialing' && subscription.trial_end) {
    return new Date(subscription.trial_end) > new Date();
  }

  return false;
}

// Dias restantes do trial
export async function getTrialDaysRemaining(): Promise<number> {
  const subscription = await getCurrentSubscription();
  if (!subscription?.trial_end) return 0;

  const trialEnd = new Date(subscription.trial_end);
  const now = new Date();
  const diff = trialEnd.getTime() - now.getTime();
  const days = Math.ceil(diff / (1000 * 60 * 60 * 24));

  return days > 0 ? days : 0;
}

// Verificar quota
export async function checkQuota(resource: 'users' | 'invoices' | 'storage'): Promise<{
  used: number;
  limit: number;
  percentage: number;
  exceeded: boolean;
}> {
  const subscription = await getCurrentSubscription();
  
  if (!subscription?.subscription_plans) {
    return { used: 0, limit: 0, percentage: 0, exceeded: false };
  }

  const limits = subscription.subscription_plans.limits;
  let limit = 0;
  let used = 0;

  switch (resource) {
    case 'users':
      limit = limits.users === -1 ? Infinity : limits.users;
      // TODO: Get actual user count
      used = 1;
      break;
    case 'invoices':
      limit = limits.invoices_per_month === -1 ? Infinity : limits.invoices_per_month;
      // TODO: Get actual invoice count this month
      used = 0;
      break;
    case 'storage':
      limit = limits.storage_gb === -1 ? Infinity : limits.storage_gb;
      // TODO: Get actual storage usage
      used = 0;
      break;
  }

  const percentage = limit === Infinity ? 0 : (used / limit) * 100;
  const exceeded = used > limit;

  return { used, limit, percentage, exceeded };
}
