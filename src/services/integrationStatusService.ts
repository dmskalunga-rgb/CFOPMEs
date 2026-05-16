// =====================================================
// KWANZACONTROL - Integration Status Service
// Verifica status das integrações (Stripe, Resend)
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export interface IntegrationStatus {
  name: string;
  configured: boolean;
  tested: boolean;
  lastCheck: string | null;
  error: string | null;
}

export interface IntegrationsHealth {
  stripe: IntegrationStatus;
  resend: IntegrationStatus;
  overall: 'healthy' | 'partial' | 'down';
}

/**
 * Verificar se Stripe está configurado
 */
export async function checkStripeStatus(): Promise<IntegrationStatus> {
  try {
    // Verificar se existe STRIPE_PUBLISHABLE_KEY no .env
    const hasPublicKey = import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY;
    
    if (!hasPublicKey) {
      return {
        name: 'Stripe',
        configured: false,
        tested: false,
        lastCheck: new Date().toISOString(),
        error: 'STRIPE_PUBLISHABLE_KEY não configurada',
      };
    }

    // Testar chamada à Edge Function
    const { data, error } = await supabase.functions.invoke('create_checkout_session_2026_04_06', {
      body: { planSlug: 'basic', billingInterval: 'monthly', test: true },
    });

    if (error) {
      return {
        name: 'Stripe',
        configured: true,
        tested: false,
        lastCheck: new Date().toISOString(),
        error: error.message,
      };
    }

    return {
      name: 'Stripe',
      configured: true,
      tested: data?.success || false,
      lastCheck: new Date().toISOString(),
      error: null,
    };
  } catch (error) {
    return {
      name: 'Stripe',
      configured: false,
      tested: false,
      lastCheck: new Date().toISOString(),
      error: error instanceof Error ? error.message : 'Erro desconhecido',
    };
  }
}

/**
 * Verificar se Resend está configurado
 */
export async function checkResendStatus(): Promise<IntegrationStatus> {
  try {
    // Verificar se existe RESEND_DOMAIN no .env (opcional)
    const hasDomain = import.meta.env.VITE_RESEND_DOMAIN;
    
    // Testar chamada à Edge Function (se existir uma de teste)
    // Por enquanto, apenas verificar se o domínio está configurado
    
    return {
      name: 'Resend',
      configured: true, // Assumir configurado se chegou aqui
      tested: false, // Não temos endpoint de teste ainda
      lastCheck: new Date().toISOString(),
      error: hasDomain ? null : 'Domínio não configurado (usando padrão)',
    };
  } catch (error) {
    return {
      name: 'Resend',
      configured: false,
      tested: false,
      lastCheck: new Date().toISOString(),
      error: error instanceof Error ? error.message : 'Erro desconhecido',
    };
  }
}

/**
 * Verificar saúde geral das integrações
 */
export async function checkIntegrationsHealth(): Promise<IntegrationsHealth> {
  const [stripe, resend] = await Promise.all([
    checkStripeStatus(),
    checkResendStatus(),
  ]);

  let overall: 'healthy' | 'partial' | 'down' = 'healthy';

  const configuredCount = [stripe.configured, resend.configured].filter(Boolean).length;
  const testedCount = [stripe.tested, resend.tested].filter(Boolean).length;

  if (configuredCount === 0) {
    overall = 'down';
  } else if (configuredCount < 2 || testedCount < configuredCount) {
    overall = 'partial';
  }

  return {
    stripe,
    resend,
    overall,
  };
}

/**
 * Obter secrets configurados no Supabase
 */
export async function getConfiguredSecrets(): Promise<string[]> {
  try {
    const { data, error } = await supabase.functions.invoke('get_secrets_list', {
      body: {},
    });

    if (error) throw error;

    return data?.secrets || [];
  } catch (error) {
    console.error('Error fetching secrets:', error);
    return [];
  }
}

/**
 * Verificar quais secrets estão faltando
 */
export async function getMissingSecrets(): Promise<string[]> {
  const required = [
    'STRIPE_SECRET_KEY',
    'STRIPE_PUBLISHABLE_KEY',
    'STRIPE_WEBHOOK_SECRET',
    'STRIPE_PRICE_BASIC',
    'STRIPE_PRICE_PROFESSIONAL',
    'STRIPE_PRICE_ENTERPRISE',
    'RESEND_API_KEY',
  ];

  const configured = await getConfiguredSecrets();
  
  return required.filter(secret => !configured.includes(secret));
}

/**
 * Formatar status para exibição
 */
export function formatIntegrationStatus(status: IntegrationStatus): {
  icon: string;
  color: string;
  message: string;
} {
  if (!status.configured) {
    return {
      icon: '⚠️',
      color: 'text-yellow-600',
      message: 'Não configurado',
    };
  }

  if (status.error) {
    return {
      icon: '❌',
      color: 'text-destructive',
      message: status.error,
    };
  }

  if (status.tested) {
    return {
      icon: '✅',
      color: 'text-emerald-600',
      message: 'Funcionando',
    };
  }

  return {
    icon: '🔄',
    color: 'text-blue-600',
    message: 'Configurado (não testado)',
  };
}
