import { supabase } from '@/integrations/supabase/client';

// =====================================================
// IAM SERVICE - Identity & Access Management
// =====================================================

export interface RiskAssessment {
  riskScore: number;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  requiresMFA: boolean;
  shouldBlock: boolean;
  reasons: string[];
}

export interface AccessDecision {
  allowed: boolean;
  reason: string;
  matchedRoles: string[];
  matchedPermissions: string[];
  evaluatedConditions: any[];
  matchedPolicies?: any[];
  effectiveRoles?: string[];
  evaluationTimeMs?: number;
  cached?: boolean;
}

export interface ABACPolicy {
  id: string;
  organization_id: string;
  name: string;
  description?: string;
  policy_type: 'allow' | 'deny';
  priority: number;
  subject_conditions: any;
  resource_conditions: any;
  actions: string[];
  environment_conditions?: any;
  effect: 'allow' | 'deny';
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserAttributes {
  id: string;
  organization_id: string;
  user_id: string;
  attributes: any;
  created_at: string;
  updated_at: string;
}

export interface ProtectedResource {
  id: string;
  organization_id: string;
  resource_type: string;
  resource_id: string;
  attributes: any;
  applicable_policies: string[];
  created_at: string;
  updated_at: string;
}

export interface UserProfile {
  id: string;
  organization_id: string;
  full_name: string;
  email: string;
  phone?: string;
  avatar_url?: string;
  department?: string;
  location?: string;
  status: 'active' | 'inactive' | 'suspended' | 'locked';
  last_login_at?: string;
  last_login_ip?: string;
  last_login_device?: string;
  failed_login_attempts: number;
  locked_until?: string;
  metadata?: any;
  created_at: string;
  updated_at: string;
}

export interface Role {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  description?: string;
  permissions: string[];
  is_system: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
}

export interface SecurityAlert {
  id: string;
  organization_id: string;
  user_id?: string;
  alert_type: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  title: string;
  description?: string;
  event_data?: any;
  status: 'open' | 'investigating' | 'resolved' | 'false_positive';
  resolved_by?: string;
  resolved_at?: string;
  created_at: string;
}

export interface UEBAEvent {
  id: string;
  user_id: string;
  event_type: string;
  event_data: any;
  risk_score: number;
  anomaly_detected: boolean;
  anomaly_reason?: string;
  ip_address?: string;
  device_fingerprint?: string;
  location?: any;
  timestamp: string;
}

// =====================================================
// AUTENTICAÇÃO ADAPTATIVA
// =====================================================

export const iamService = {
  // Autenticação adaptativa
  async authenticateAdaptive(params: {
    userId: string;
    organizationId: string;
    ipAddress: string;
    userAgent: string;
    deviceFingerprint: string;
    location?: {
      country: string;
      city: string;
      latitude: number;
      longitude: number;
    };
  }): Promise<{ success: boolean; riskAssessment?: RiskAssessment; error?: string }> {
    try {
      const { data, error } = await supabase.functions.invoke('iam_adaptive_auth_2026_04_06', {
        body: params
      });

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro na autenticação adaptativa:', error);
      return { success: false, error: error.message };
    }
  },

  // =====================================================
  // USUÁRIOS
  // =====================================================

  async getUsers(organizationId: string): Promise<UserProfile[]> {
    try {
      const { data, error } = await supabase
        .from('user_profiles_iam')
        .select('*')
        .eq('organization_id', organizationId)
        .order('created_at', { ascending: false });

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar usuários:', error);
      return [];
    }
  },

  async getUser(userId: string): Promise<UserProfile | null> {
    try {
      const { data, error } = await supabase
        .from('user_profiles_iam')
        .select('*')
        .eq('id', userId)
        .single();

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao buscar usuário:', error);
      return null;
    }
  },

  async updateUser(userId: string, updates: Partial<UserProfile>): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('user_profiles_iam')
        .update(updates)
        .eq('id', userId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao atualizar usuário:', error);
      return false;
    }
  },

  async suspendUser(userId: string): Promise<boolean> {
    return this.updateUser(userId, { status: 'suspended' });
  },

  async activateUser(userId: string): Promise<boolean> {
    return this.updateUser(userId, { status: 'active', failed_login_attempts: 0, locked_until: null });
  },

  // =====================================================
  // ROLES
  // =====================================================

  async getRoles(organizationId: string): Promise<Role[]> {
    try {
      const { data, error } = await supabase
        .from('iam_roles')
        .select('*')
        .eq('organization_id', organizationId)
        .order('priority', { ascending: false });

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar roles:', error);
      return [];
    }
  },

  async createRole(role: Omit<Role, 'id' | 'created_at' | 'updated_at'>): Promise<Role | null> {
    try {
      const { data, error } = await supabase
        .from('iam_roles')
        .insert(role)
        .select()
        .single();

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao criar role:', error);
      return null;
    }
  },

  async updateRole(roleId: string, updates: Partial<Role>): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('iam_roles')
        .update(updates)
        .eq('id', roleId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao atualizar role:', error);
      return false;
    }
  },

  async deleteRole(roleId: string): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('iam_roles')
        .delete()
        .eq('id', roleId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao deletar role:', error);
      return false;
    }
  },

  async assignRole(userId: string, roleId: string, assignedBy: string): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('iam_user_roles')
        .insert({
          user_id: userId,
          role_id: roleId,
          assigned_by: assignedBy
        });

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao atribuir role:', error);
      return false;
    }
  },

  async removeRole(userId: string, roleId: string): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('iam_user_roles')
        .delete()
        .eq('user_id', userId)
        .eq('role_id', roleId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao remover role:', error);
      return false;
    }
  },

  // =====================================================
  // ALERTAS DE SEGURANÇA
  // =====================================================

  async getSecurityAlerts(organizationId: string, status?: string): Promise<SecurityAlert[]> {
    try {
      let query = supabase
        .from('iam_security_alerts')
        .select('*')
        .eq('organization_id', organizationId);

      if (status) {
        query = query.eq('status', status);
      }

      const { data, error } = await query.order('created_at', { ascending: false });

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar alertas:', error);
      return [];
    }
  },

  async resolveAlert(alertId: string, resolvedBy: string): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('iam_security_alerts')
        .update({
          status: 'resolved',
          resolved_by: resolvedBy,
          resolved_at: new Date().toISOString()
        })
        .eq('id', alertId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao resolver alerta:', error);
      return false;
    }
  },

  // =====================================================
  // EVENTOS UEBA
  // =====================================================

  async getUEBAEvents(userId: string, limit: number = 100): Promise<UEBAEvent[]> {
    try {
      const { data, error } = await supabase
        .from('iam_ueba_events')
        .select('*')
        .eq('user_id', userId)
        .order('timestamp', { ascending: false })
        .limit(limit);

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar eventos UEBA:', error);
      return [];
    }
  },

  async getAnomalies(organizationId: string): Promise<UEBAEvent[]> {
    try {
      const { data, error } = await supabase
        .from('iam_ueba_events')
        .select('*')
        .eq('anomaly_detected', true)
        .order('timestamp', { ascending: false })
        .limit(50);

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar anomalias:', error);
      return [];
    }
  },

  // =====================================================
  // SESSÕES
  // =====================================================

  async getActiveSessions(userId: string): Promise<any[]> {
    try {
      const { data, error } = await supabase
        .from('iam_user_sessions')
        .select('*')
        .eq('user_id', userId)
        .gte('expires_at', new Date().toISOString())
        .order('created_at', { ascending: false });

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar sessões:', error);
      return [];
    }
  },

  async revokeSession(sessionId: string): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('iam_user_sessions')
        .delete()
        .eq('id', sessionId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao revogar sessão:', error);
      return false;
    }
  },

  // =====================================================
  // RBAC + ABAC - Controle de Acesso Avançado
  // =====================================================

  /**
   * Verificar acesso usando RBAC + ABAC
   */
  async checkAccess(params: {
    userId: string;
    organizationId: string;
    resourceType: string;
    resourceId?: string;
    action: string;
    context?: any;
  }): Promise<AccessDecision> {
    try {
      const { data, error } = await supabase.functions.invoke(
        'rbac_abac_engine_advanced_2026_04_06',
        {
          body: {
            action: 'check_access',
            params
          }
        }
      );

      if (error) throw error;
      return data.decision;
    } catch (error: any) {
      console.error('Erro ao verificar acesso:', error);
      return {
        allowed: false,
        reason: 'Erro ao verificar acesso',
        matchedRoles: [],
        matchedPermissions: [],
        evaluatedConditions: []
      };
    }
  },

  /**
   * Obter todas as permissões de um usuário
   */
  async getUserPermissions(userId: string, organizationId: string): Promise<{
    permissions: string[];
    effectiveRoles: string[];
  }> {
    try {
      const { data, error } = await supabase.functions.invoke(
        'rbac_abac_engine_advanced_2026_04_06',
        {
          body: {
            action: 'get_user_permissions',
            params: { userId, organizationId }
          }
        }
      );

      if (error) throw error;
      return {
        permissions: data.permissions || [],
        effectiveRoles: data.effectiveRoles || []
      };
    } catch (error: any) {
      console.error('Erro ao obter permissões:', error);
      return { permissions: [], effectiveRoles: [] };
    }
  },

  /**
   * Criar política ABAC
   */
  async createABACPolicy(organizationId: string, policy: Partial<ABACPolicy>): Promise<ABACPolicy | null> {
    try {
      const { data, error } = await supabase.functions.invoke(
        'rbac_abac_engine_advanced_2026_04_06',
        {
          body: {
            action: 'create_abac_policy',
            params: { organizationId, policy }
          }
        }
      );

      if (error) throw error;
      return data.policy;
    } catch (error: any) {
      console.error('Erro ao criar política ABAC:', error);
      return null;
    }
  },

  /**
   * Listar políticas ABAC
   */
  async getABACPolicies(organizationId: string): Promise<ABACPolicy[]> {
    try {
      const { data, error } = await supabase.functions.invoke(
        'rbac_abac_engine_advanced_2026_04_06',
        {
          body: {
            action: 'get_abac_policies',
            params: { organizationId }
          }
        }
      );

      if (error) throw error;
      return data.policies || [];
    } catch (error: any) {
      console.error('Erro ao listar políticas ABAC:', error);
      return [];
    }
  },

  /**
   * Atualizar política ABAC
   */
  async updateABACPolicy(policyId: string, updates: Partial<ABACPolicy>): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('abac_policies')
        .update(updates)
        .eq('id', policyId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao atualizar política ABAC:', error);
      return false;
    }
  },

  /**
   * Deletar política ABAC
   */
  async deleteABACPolicy(policyId: string): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('abac_policies')
        .delete()
        .eq('id', policyId);

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao deletar política ABAC:', error);
      return false;
    }
  },

  /**
   * Obter atributos de usuário
   */
  async getUserAttributes(userId: string, organizationId: string): Promise<UserAttributes | null> {
    try {
      const { data, error } = await supabase
        .from('iam_user_attributes')
        .select('*')
        .eq('user_id', userId)
        .eq('organization_id', organizationId)
        .single();

      if (error) throw error;
      return data;
    } catch (error: any) {
      console.error('Erro ao obter atributos:', error);
      return null;
    }
  },

  /**
   * Atualizar atributos de usuário
   */
  async updateUserAttributes(
    userId: string,
    organizationId: string,
    attributes: any
  ): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('iam_user_attributes')
        .upsert({
          user_id: userId,
          organization_id: organizationId,
          attributes
        }, { onConflict: 'organization_id,user_id' });

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao atualizar atributos:', error);
      return false;
    }
  },

  /**
   * Registrar recurso protegido
   */
  async registerProtectedResource(
    organizationId: string,
    resourceType: string,
    resourceId: string,
    attributes: any
  ): Promise<boolean> {
    try {
      const { error } = await supabase
        .from('iam_protected_resources')
        .upsert({
          organization_id: organizationId,
          resource_type: resourceType,
          resource_id: resourceId,
          attributes
        }, { onConflict: 'organization_id,resource_type,resource_id' });

      if (error) throw error;
      return true;
    } catch (error: any) {
      console.error('Erro ao registrar recurso:', error);
      return false;
    }
  },

  /**
   * Obter decisões de acesso (audit)
   */
  async getAccessDecisions(
    organizationId: string,
    filters?: {
      userId?: string;
      resourceType?: string;
      startDate?: string;
      endDate?: string;
    }
  ): Promise<any[]> {
    try {
      let query = supabase
        .from('iam_access_decisions')
        .select('*')
        .eq('organization_id', organizationId)
        .order('created_at', { ascending: false })
        .limit(100);

      if (filters?.userId) {
        query = query.eq('user_id', filters.userId);
      }

      if (filters?.resourceType) {
        query = query.eq('resource_type', filters.resourceType);
      }

      if (filters?.startDate) {
        query = query.gte('created_at', filters.startDate);
      }

      if (filters?.endDate) {
        query = query.lte('created_at', filters.endDate);
      }

      const { data, error } = await query;

      if (error) throw error;
      return data || [];
    } catch (error: any) {
      console.error('Erro ao obter decisões de acesso:', error);
      return [];
    }
  },

  /**
   * Invalidar cache de permissões
   */
  async invalidatePermissionCache(
    organizationId: string,
    userId?: string
  ): Promise<boolean> {
    try {
      const { data, error } = await supabase.functions.invoke(
        'rbac_abac_engine_advanced_2026_04_06',
        {
          body: {
            action: 'invalidate_cache',
            params: { organizationId, userId }
          }
        }
      );

      if (error) throw error;
      return data.success;
    } catch (error: any) {
      console.error('Erro ao invalidar cache:', error);
      return false;
    }
  }
};
