import { supabase } from '@/integrations/supabase/client';

export interface APIKey {
  id: string;
  tenant_id: string;
  key_name: string;
  api_key: string;
  key_type: 'PUBLIC' | 'PRIVATE';
  permissions: string[];
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateAPIKeyInput {
  name: string;
  permissions: string[];
  key_type?: 'PUBLIC' | 'PRIVATE';
  expires_at?: string;
}

export interface APIKeyWithSecret extends Omit<APIKey, 'api_key'> {
  key: string; // Only returned on creation
}

class APIKeysService {
  async listAPIKeys(tenantId: string): Promise<APIKey[]> {
    try {
      const { data, error } = await supabase
        .from('api_keys')
        .select('*')
        .eq('tenant_id', tenantId)
        .order('created_at', { ascending: false });

      if (error) {
        console.error('Erro ao listar API Keys:', error);
        // Se a tabela não existe, retornar array vazio
        if (error.code === '42P01' || error.message.includes('does not exist')) {
          return [];
        }
        throw error;
      }
      
      return data || [];
    } catch (error: any) {
      console.error('Erro ao listar API Keys:', error);
      // Retornar array vazio em caso de erro
      return [];
    }
  }

  async createAPIKey(tenantId: string, input: CreateAPIKeyInput): Promise<APIKeyWithSecret> {
    try {
      // Generate API key
      const key = this.generateAPIKey();

      const { data: { user } } = await supabase.auth.getUser();
      if (!user) throw new Error('Usuário não autenticado');

      const { data, error } = await supabase
        .from('api_keys')
        .insert({
          tenant_id: tenantId,
          key_name: input.name,
          api_key: key,
          key_type: input.key_type || 'PUBLIC',
          permissions: input.permissions,
          expires_at: input.expires_at,
          created_by: user.id,
          is_active: true,
        })
        .select()
        .single();

      if (error) {
        console.error('Erro ao criar API Key:', error);
        throw new Error(`Erro ao criar API Key: ${error.message}`);
      }

      return {
        ...data,
        key, // Return the actual key only once
      };
    } catch (error: any) {
      console.error('Erro ao criar API Key:', error);
      throw error;
    }
  }

  async revokeAPIKey(id: string): Promise<void> {
    try {
      const { error } = await supabase
        .from('api_keys')
        .update({ is_active: false })
        .eq('id', id);

      if (error) {
        console.error('Erro ao revogar API Key:', error);
        throw new Error(`Erro ao revogar API Key: ${error.message}`);
      }
    } catch (error: any) {
      console.error('Erro ao revogar API Key:', error);
      throw error;
    }
  }

  async deleteAPIKey(id: string): Promise<void> {
    try {
      const { error } = await supabase
        .from('api_keys')
        .delete()
        .eq('id', id);

      if (error) {
        console.error('Erro ao deletar API Key:', error);
        throw new Error(`Erro ao deletar API Key: ${error.message}`);
      }
    } catch (error: any) {
      console.error('Erro ao deletar API Key:', error);
      throw error;
    }
  }

  async getAPIKeyUsage(apiKeyId: string, days: number = 30): Promise<any[]> {
    try {
      const startDate = new Date();
      startDate.setDate(startDate.getDate() - days);

      const { data, error } = await supabase
        .from('api_usage_logs')
        .select('*')
        .eq('api_key_id', apiKeyId)
        .gte('created_at', startDate.toISOString())
        .order('created_at', { ascending: false })
        .limit(1000);

      if (error) {
        console.error('Erro ao buscar uso da API Key:', error);
        // Se a tabela não existe, retornar array vazio
        if (error.code === '42P01' || error.message.includes('does not exist')) {
          return [];
        }
        throw error;
      }
      
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar uso da API Key:', error);
      return [];
    }
  }

  private generateAPIKey(): string {
    const prefix = 'kc_live_';
    const randomPart = Array.from({ length: 32 }, () =>
      Math.random().toString(36).charAt(2)
    ).join('');
    return prefix + randomPart;
  }
}

export const apiKeysService = new APIKeysService();
