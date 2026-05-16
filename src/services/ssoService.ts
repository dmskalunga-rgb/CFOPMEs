// =====================================================
// KWANZACONTROL - SSO Service
// Serviço de Single Sign-On (OAuth 2.0 / SAML)
// Data: 2026-04-04
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export type SSOProvider = 'google' | 'microsoft' | 'github' | 'saml';

export interface SSOConfig {
  id: string;
  tenant_id: string;
  provider: SSOProvider;
  enabled: boolean;
  client_id: string;
  client_secret: string;
  redirect_uri: string;
  scopes: string[];
  metadata?: Record<string, any>;
  created_at: string;
}

export interface SAMLConfig {
  entity_id: string;
  sso_url: string;
  certificate: string;
  sign_requests: boolean;
  encrypt_assertions: boolean;
}

export const ssoService = {
  /**
   * Configurar provedor SSO
   */
  async configureSSOProvider(config: Omit<SSOConfig, 'id' | 'created_at'>): Promise<SSOConfig> {
    const { data, error } = await supabase
      .from('sso_configurations')
      .insert(config)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Obter configurações SSO
   */
  async getSSOConfigs(tenantId: string): Promise<SSOConfig[]> {
    const { data, error } = await supabase
      .from('sso_configurations')
      .select('*')
      .eq('tenant_id', tenantId);

    if (error) throw error;
    return data || [];
  },

  /**
   * Atualizar configuração SSO
   */
  async updateSSOConfig(configId: string, updates: Partial<SSOConfig>): Promise<SSOConfig> {
    const { data, error } = await supabase
      .from('sso_configurations')
      .update(updates)
      .eq('id', configId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Remover configuração SSO
   */
  async removeSSOConfig(configId: string): Promise<void> {
    const { error } = await supabase
      .from('sso_configurations')
      .delete()
      .eq('id', configId);

    if (error) throw error;
  },

  /**
   * Iniciar fluxo OAuth
   */
  async initiateOAuth(provider: SSOProvider, tenantId: string): Promise<{ url: string }> {
    const { data, error } = await supabase.functions.invoke('sso-oauth-initiate', {
      body: { provider, tenant_id: tenantId },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Callback OAuth
   */
  async handleOAuthCallback(provider: SSOProvider, code: string, state: string): Promise<any> {
    const { data, error } = await supabase.functions.invoke('sso-oauth-callback', {
      body: { provider, code, state },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Configurar SAML
   */
  async configureSAML(tenantId: string, samlConfig: SAMLConfig): Promise<void> {
    const { error } = await supabase
      .from('sso_configurations')
      .upsert({
        tenant_id: tenantId,
        provider: 'saml',
        enabled: true,
        metadata: samlConfig,
      });

    if (error) throw error;
  },

  /**
   * Obter metadados SAML
   */
  async getSAMLMetadata(tenantId: string): Promise<string> {
    const { data, error } = await supabase.functions.invoke('sso-saml-metadata', {
      body: { tenant_id: tenantId },
    });

    if (error) throw error;
    return data.metadata;
  },

  /**
   * Processar resposta SAML
   */
  async processSAMLResponse(samlResponse: string): Promise<any> {
    const { data, error } = await supabase.functions.invoke('sso-saml-process', {
      body: { saml_response: samlResponse },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Habilitar/Desabilitar provedor SSO
   */
  async toggleSSOProvider(configId: string, enabled: boolean): Promise<void> {
    const { error } = await supabase
      .from('sso_configurations')
      .update({ enabled })
      .eq('id', configId);

    if (error) throw error;
  },

  /**
   * Obter logs de autenticação SSO
   */
  async getSSOLogs(tenantId: string, limit = 50): Promise<any[]> {
    const { data, error } = await supabase
      .from('sso_auth_logs')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false })
      .limit(limit);

    if (error) throw error;
    return data || [];
  },

  /**
   * Login com Google
   */
  async signInWithGoogle(): Promise<void> {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    });

    if (error) throw error;
  },

  /**
   * Login com Microsoft
   */
  async signInWithMicrosoft(): Promise<void> {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'azure',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
        scopes: 'email profile openid',
      },
    });

    if (error) throw error;
  },

  /**
   * Login com GitHub
   */
  async signInWithGitHub(): Promise<void> {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'github',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    });

    if (error) throw error;
  },
};
