// =====================================================
// KWANZACONTROL - Active Directory Integration Service
// Serviço de integração com Active Directory (LDAP)
// Data: 2026-04-04
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export interface ADUser {
  distinguishedName: string;
  userPrincipalName: string;
  sAMAccountName: string;
  displayName: string;
  givenName: string;
  surname: string;
  mail: string;
  department: string;
  title: string;
  telephoneNumber: string;
  memberOf: string[];
}

export interface ADConfig {
  id: string;
  tenant_id: string;
  server_url: string;
  base_dn: string;
  bind_dn: string;
  bind_password: string;
  sync_enabled: boolean;
  sync_interval: number;
  last_sync: string | null;
  created_at: string;
}

export interface ADSyncResult {
  success: boolean;
  users_synced: number;
  users_created: number;
  users_updated: number;
  users_disabled: number;
  errors: string[];
  sync_time: string;
}

export const activeDirectoryService = {
  /**
   * Configurar integração com Active Directory
   */
  async configureAD(config: Omit<ADConfig, 'id' | 'created_at' | 'last_sync'>): Promise<ADConfig> {
    const { data, error } = await supabase
      .from('ad_configurations')
      .insert(config)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Obter configuração do AD
   */
  async getADConfig(tenantId: string): Promise<ADConfig | null> {
    const { data, error } = await supabase
      .from('ad_configurations')
      .select('*')
      .eq('tenant_id', tenantId)
      .single();

    if (error && error.code !== 'PGRST116') throw error;
    return data;
  },

  /**
   * Atualizar configuração do AD
   */
  async updateADConfig(configId: string, updates: Partial<ADConfig>): Promise<ADConfig> {
    const { data, error } = await supabase
      .from('ad_configurations')
      .update(updates)
      .eq('id', configId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Testar conexão com AD
   */
  async testADConnection(config: Partial<ADConfig>): Promise<{ success: boolean; message: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('ad-test-connection', {
        body: {
          server_url: config.server_url,
          base_dn: config.base_dn,
          bind_dn: config.bind_dn,
          bind_password: config.bind_password,
        },
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      return {
        success: false,
        message: error.message || 'Erro ao testar conexão com AD',
      };
    }
  },

  /**
   * Sincronizar utilizadores do AD
   */
  async syncADUsers(tenantId: string): Promise<ADSyncResult> {
    try {
      const { data, error } = await supabase.functions.invoke('ad-sync-users', {
        body: { tenant_id: tenantId },
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      return {
        success: false,
        users_synced: 0,
        users_created: 0,
        users_updated: 0,
        users_disabled: 0,
        errors: [error.message || 'Erro ao sincronizar utilizadores'],
        sync_time: new Date().toISOString(),
      };
    }
  },

  /**
   * Obter histórico de sincronizações
   */
  async getSyncHistory(tenantId: string, limit = 10): Promise<ADSyncResult[]> {
    const { data, error } = await supabase
      .from('ad_sync_history')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('sync_time', { ascending: false })
      .limit(limit);

    if (error) throw error;
    return data || [];
  },

  /**
   * Mapear grupos do AD para roles
   */
  async mapADGroupToRole(tenantId: string, adGroup: string, roleId: string): Promise<void> {
    const { error } = await supabase
      .from('ad_group_role_mappings')
      .insert({
        tenant_id: tenantId,
        ad_group: adGroup,
        role_id: roleId,
      });

    if (error) throw error;
  },

  /**
   * Obter mapeamentos de grupos
   */
  async getGroupMappings(tenantId: string): Promise<any[]> {
    const { data, error } = await supabase
      .from('ad_group_role_mappings')
      .select('*, roles(*)')
      .eq('tenant_id', tenantId);

    if (error) throw error;
    return data || [];
  },

  /**
   * Remover mapeamento de grupo
   */
  async removeGroupMapping(mappingId: string): Promise<void> {
    const { error } = await supabase
      .from('ad_group_role_mappings')
      .delete()
      .eq('id', mappingId);

    if (error) throw error;
  },

  /**
   * Habilitar/Desabilitar sincronização automática
   */
  async toggleAutoSync(configId: string, enabled: boolean): Promise<void> {
    const { error } = await supabase
      .from('ad_configurations')
      .update({ sync_enabled: enabled })
      .eq('id', configId);

    if (error) throw error;
  },
};
