import { supabase } from '@/integrations/supabase/client';

// =====================================================
// PAM SERVICE - Privileged Access Management
// =====================================================

export interface Secret {
  id: string;
  organization_id: string;
  name: string;
  secret_type: 'password' | 'api_key' | 'token' | 'certificate' | 'ssh_key';
  encrypted_value: string;
  encryption_key_id: string;
  metadata?: any;
  tags?: string[];
  rotation_policy?: {
    enabled: boolean;
    intervalDays: number;
  };
  last_rotated_at?: string;
  next_rotation_at?: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface AccessRequest {
  id: string;
  organization_id: string;
  user_id: string;
  secret_id: string;
  reason: string;
  requested_duration: number;
  status: 'pending' | 'approved' | 'denied' | 'expired' | 'revoked';
  approved_by?: string;
  approved_at?: string;
  expires_at?: string;
  accessed_at?: string;
  revoked_by?: string;
  revoked_at?: string;
  created_at: string;
}

export interface PrivilegedSession {
  id: string;
  access_request_id: string;
  user_id: string;
  session_data?: any;
  recording_url?: string;
  commands_executed?: any[];
  anomalies_detected?: any[];
  started_at: string;
  ended_at?: string;
}

export interface AuditLog {
  id: string;
  organization_id: string;
  user_id?: string;
  session_id?: string;
  action: string;
  resource?: string;
  details?: any;
  risk_score: number;
  timestamp: string;
}

// =====================================================
// VAULT MANAGER
// =====================================================

export const pamService = {
  // =====================================================
  // SECRETS (VAULT)
  // =====================================================

  async createSecret(params: {
    organizationId: string;
    userId: string;
    secretData: {
      name: string;
      secretType: 'password' | 'api_key' | 'token' | 'certificate' | 'ssh_key';
      value: string;
      metadata?: any;
      tags?: string[];
      rotationPolicy?: {
        enabled: boolean;
        intervalDays: number;
      };
    };
  }): Promise<{ success: boolean; secret?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_vault_manager_2026_04_06', {
        body: {
          action: 'create',
          ...params
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao criar segredo:', error);
      return { success: false, error: error.message };
    }
  },

  async getSecret(params: {
    organizationId: string;
    userId: string;
    secretId: string;
  }): Promise<{ success: boolean; secret?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_vault_manager_2026_04_06', {
        body: {
          action: 'get',
          ...params
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao buscar segredo:', error);
      return { success: false, error: error.message };
    }
  },

  async updateSecret(params: {
    secretId: string;
    secretData: {
      value?: string;
      metadata?: any;
      tags?: string[];
      rotationPolicy?: any;
    };
  }): Promise<{ success: boolean; secret?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_vault_manager_2026_04_06', {
        body: {
          action: 'update',
          ...params
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao atualizar segredo:', error);
      return { success: false, error: error.message };
    }
  },

  async deleteSecret(secretId: string): Promise<{ success: boolean; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_vault_manager_2026_04_06', {
        body: {
          action: 'delete',
          secretId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao deletar segredo:', error);
      return { success: false, error: error.message };
    }
  },

  async rotateSecret(secretId: string): Promise<{ success: boolean; newValue?: string; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_vault_manager_2026_04_06', {
        body: {
          action: 'rotate',
          secretId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao rotacionar segredo:', error);
      return { success: false, error: error.message };
    }
  },

  async listSecrets(organizationId: string): Promise<{ success: boolean; secrets?: any[]; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_vault_manager_2026_04_06', {
        body: {
          action: 'list',
          organizationId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao listar segredos:', error);
      return { success: false, error: error.message };
    }
  },

  // =====================================================
  // JIT ACCESS (Just-in-Time)
  // =====================================================

  async requestAccess(params: {
    organizationId: string;
    userId: string;
    requestData: {
      secretId: string;
      reason: string;
      durationMinutes: number;
    };
  }): Promise<{ success: boolean; request?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_jit_access_workflow_2026_04_06', {
        body: {
          action: 'request',
          ...params
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao solicitar acesso:', error);
      return { success: false, error: error.message };
    }
  },

  async approveAccess(requestId: string, userId: string): Promise<{ success: boolean; request?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_jit_access_workflow_2026_04_06', {
        body: {
          action: 'approve',
          requestId,
          userId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao aprovar acesso:', error);
      return { success: false, error: error.message };
    }
  },

  async denyAccess(requestId: string, userId: string): Promise<{ success: boolean; request?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_jit_access_workflow_2026_04_06', {
        body: {
          action: 'deny',
          requestId,
          userId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao negar acesso:', error);
      return { success: false, error: error.message };
    }
  },

  async revokeAccess(requestId: string, userId: string): Promise<{ success: boolean; request?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_jit_access_workflow_2026_04_06', {
        body: {
          action: 'revoke',
          requestId,
          userId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao revogar acesso:', error);
      return { success: false, error: error.message };
    }
  },

  async listAccessRequests(organizationId: string, userId: string): Promise<{ success: boolean; requests?: any[]; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_jit_access_workflow_2026_04_06', {
        body: {
          action: 'list',
          organizationId,
          userId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao listar solicitações:', error);
      return { success: false, error: error.message };
    }
  },

  async accessSecret(requestId: string, userId: string): Promise<{ success: boolean; sessionId?: string; secret?: any; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('pam_jit_access_workflow_2026_04_06', {
        body: {
          action: 'access',
          requestId,
          userId
        }
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao acessar segredo:', error);
      return { success: false, error: error.message };
    }
  },

  // =====================================================
  // SESSÕES PRIVILEGIADAS
  // =====================================================

  async getSessions(userId?: string): Promise<PrivilegedSession[]> {
    try {
      let query = supabase
        .from('pam_privileged_sessions')
        .select('*')
        .order('started_at', { ascending: false });

      if (userId) {
        query = query.eq('user_id', userId);
      }

      const { data, error } = await query;

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar sessões:', error);
      return [];
    }
  },

  async endSession(sessionId: string): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('pam_privileged_sessions')
        .update({ ended_at: new Date().toISOString() })
        .eq('id', sessionId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao encerrar sessão:', error);
      return false;
    }
  },

  // =====================================================
  // AUDIT LOGS
  // =====================================================

  async getAuditLogs(organizationId: string, filters?: {
    userId?: string;
    action?: string;
    startDate?: string;
    endDate?: string;
  }): Promise<AuditLog[]> {
    try {
      let query = supabase
        .from('pam_audit_trail')
        .select('*')
        .eq('organization_id', organizationId);

      if (filters?.userId) {
        query = query.eq('user_id', filters.userId);
      }

      if (filters?.action) {
        query = query.eq('action', filters.action);
      }

      if (filters?.startDate) {
        query = query.gte('timestamp', filters.startDate);
      }

      if (filters?.endDate) {
        query = query.lte('timestamp', filters.endDate);
      }

      const { data, error } = await query.order('timestamp', { ascending: false }).limit(1000);

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar logs de auditoria:', error);
      return [];
    }
  }
};
