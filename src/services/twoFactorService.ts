// =====================================================
// KWANZACONTROL - Two Factor Service
// Serviços para autenticação de dois fatores
// Data: 2026-04-04
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export const twoFactorService = {
  /**
   * Gerar código 2FA
   */
  async generateCode(method: 'EMAIL' | 'SMS', email?: string, phone?: string) {
    const { data, error } = await supabase.functions.invoke('two_factor_auth_2026_04_04', {
      body: {
        action: 'generate',
        method,
        email,
        phone,
      },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Verificar código 2FA
   */
  async verifyCode(code: string, userId: string) {
    const { data, error } = await supabase.functions.invoke('two_factor_auth_2026_04_04', {
      body: {
        action: 'verify',
        code,
        user_id: userId,
      },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Ativar 2FA para utilizador
   */
  async enable2FA(userId: string, method: 'EMAIL' | 'SMS') {
    const { data, error } = await supabase
      .from('users')
      .update({
        two_factor_enabled: true,
        two_factor_method: method,
      })
      .eq('id', userId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Desativar 2FA para utilizador
   */
  async disable2FA(userId: string) {
    const { data, error } = await supabase
      .from('users')
      .update({
        two_factor_enabled: false,
        two_factor_method: null,
      })
      .eq('id', userId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Verificar se 2FA está ativado
   */
  async is2FAEnabled(userId: string) {
    const { data, error } = await supabase
      .from('users')
      .select('two_factor_enabled, two_factor_method')
      .eq('id', userId)
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Buscar códigos 2FA do utilizador
   */
  async getCodes(userId: string) {
    const { data, error } = await supabase
      .from('two_factor_codes')
      .select('*')
      .eq('user_id', userId)
      .order('created_at', { ascending: false })
      .limit(10);

    if (error) throw error;
    return data;
  },
};
