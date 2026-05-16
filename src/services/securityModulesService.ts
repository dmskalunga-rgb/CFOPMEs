// =====================================================
// KWANZACONTROL - IAM, PAM, Billing, RBAC Service
// Serviço para chamar Edge Functions
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// IAM SERVICE
// =====================================================
export const iamService = {
  async getUsers(organizationId: string, status?: string, role?: string) {
    const { data, error } = await supabase.functions.invoke('iam_management_fixed_2026_04_07', {
      body: { action: 'get_users', data: { organizationId, status, role } },
    });
    if (error) throw error;
    return data.data;
  },

  async createUser(userData: any) {
    const { data, error } = await supabase.functions.invoke('iam_management_fixed_2026_04_07', {
      body: { action: 'create_user', data: userData },
    });
    if (error) throw error;
    return data.data;
  },

  async updateUser(user_id: string, updates: any, actor_id: string) {
    const { data, error } = await supabase.functions.invoke('iam_management_fixed_2026_04_07', {
      body: { action: 'update_user', data: { user_id, updates, actor_id } },
    });
    if (error) throw error;
    return data.data;
  },

  async deleteUser(user_id: string, actor_id: string) {
    const { data, error } = await supabase.functions.invoke('iam_management_fixed_2026_04_07', {
      body: { action: 'delete_user', data: { user_id, actor_id } },
    });
    if (error) throw error;
    return data.data;
  },

  async getSessions(organizationId: string, userId?: string) {
    const { data, error } = await supabase.functions.invoke('iam_management_fixed_2026_04_07', {
      body: { action: 'get_sessions', data: { organizationId, userId } },
    });
    if (error) throw error;
    return data.data;
  },

  async revokeSession(session_id: string, actor_id: string) {
    const { data, error } = await supabase.functions.invoke('iam_management_fixed_2026_04_07', {
      body: { action: 'revoke_session', data: { session_id, actor_id } },
    });
    if (error) throw error;
    return data.data;
  },

  async getAuditLogs(filters: any) {
    const { data, error } = await supabase.functions.invoke('iam_management_fixed_2026_04_07', {
      body: { action: 'get_audit_logs', data: filters },
    });
    if (error) throw error;
    return data.data;
  },

  async getAnalytics(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('iam_management_fixed_2026_04_07', {
      body: { action: 'get_analytics', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },
};

// =====================================================
// PAM SERVICE
// =====================================================
export const pamService = {
  async requestAccess(requestData: any) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'request_access', data: requestData },
    });
    if (error) throw error;
    return data.data;
  },

  async approveAccess(request_id: string, approver_id: string, organization_id: string) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'approve_access', data: { request_id, approver_id, organization_id } },
    });
    if (error) throw error;
    return data.data;
  },

  async revokeAccess(session_id: string, revoker_id: string, organization_id: string, reason: string) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'revoke_access', data: { session_id, revoker_id, organization_id, reason } },
    });
    if (error) throw error;
    return data.data;
  },

  async getRequests(organizationId: string, status?: string, userId?: string) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'get_requests', data: { organizationId, status, userId } },
    });
    if (error) throw error;
    return data.data;
  },

  async getActiveSessions(organizationId: string, userId?: string) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'get_active_sessions', data: { organizationId, userId } },
    });
    if (error) throw error;
    return data.data;
  },

  async getPrivilegedUsers(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'get_privileged_users', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },

  async getAnalytics(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'get_analytics', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },

  async createVaultSecret(secretData: any) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'create_vault_secret', data: secretData },
    });
    if (error) throw error;
    return data.data;
  },

  async getVaultSecrets(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('pam_management_fixed_2026_04_07', {
      body: { action: 'get_vault_secrets', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },
};

// =====================================================
// BILLING SERVICE
// =====================================================
export const billingService = {
  async getOverview(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('billing_dashboard_fixed_2026_04_07', {
      body: { action: 'get_overview', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },

  async getSubscriptions(organizationId: string, status?: string) {
    const { data, error } = await supabase.functions.invoke('billing_dashboard_fixed_2026_04_07', {
      body: { action: 'get_subscriptions', data: { organizationId, status } },
    });
    if (error) throw error;
    return data.data;
  },

  async getUsage(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('billing_dashboard_fixed_2026_04_07', {
      body: { action: 'get_usage', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },

  async getRevenue(organizationId: string, months = 12) {
    const { data, error } = await supabase.functions.invoke('billing_dashboard_fixed_2026_04_07', {
      body: { action: 'get_revenue', data: { organizationId, months } },
    });
    if (error) throw error;
    return data.data;
  },

  async getChurnAnalysis(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('billing_dashboard_fixed_2026_04_07', {
      body: { action: 'get_churn_analysis', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },

  async getMRRTrend(organizationId: string, months = 12) {
    const { data, error } = await supabase.functions.invoke('billing_dashboard_fixed_2026_04_07', {
      body: { action: 'get_mrr_trend', data: { organizationId, months } },
    });
    if (error) throw error;
    return data.data;
  },

  async getPlanDistribution(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('billing_dashboard_fixed_2026_04_07', {
      body: { action: 'get_plan_distribution', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },

  async predictRevenue(organizationId: string, monthsAhead = 3) {
    const { data, error } = await supabase.functions.invoke('billing_dashboard_fixed_2026_04_07', {
      body: { action: 'predict_revenue', data: { organizationId, monthsAhead } },
    });
    if (error) throw error;
    return data.data;
  },
};

// =====================================================
// RBAC SERVICE
// =====================================================
export const rbacService = {
  async checkPermission(userId: string, resource: string, action: string, context?: any) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'check_permission', data: { userId, resource, action, context } },
    });
    if (error) throw error;
    return data.data;
  },

  async getRoles(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'get_roles', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },

  async createRole(roleData: any) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'create_role', data: roleData },
    });
    if (error) throw error;
    return data.data;
  },

  async updateRole(role_id: string, updates: any) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'update_role', data: { role_id, updates } },
    });
    if (error) throw error;
    return data.data;
  },

  async deleteRole(role_id: string) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'delete_role', data: { role_id } },
    });
    if (error) throw error;
    return data.data;
  },

  async getPermissions(role?: string) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'get_permissions', data: { role } },
    });
    if (error) throw error;
    return data.data;
  },

  async assignPermission(role: string, resource: string, action: string) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'assign_permission', data: { role, resource, action } },
    });
    if (error) throw error;
    return data.data;
  },

  async revokePermission(permission_id: string) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'revoke_permission', data: { permission_id } },
    });
    if (error) throw error;
    return data.data;
  },

  async getPolicies(organizationId: string) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'get_policies', data: { organizationId } },
    });
    if (error) throw error;
    return data.data;
  },

  async createPolicy(policyData: any) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'create_policy', data: policyData },
    });
    if (error) throw error;
    return data.data;
  },

  async evaluatePolicy(policy_id: string, context: any) {
    const { data, error } = await supabase.functions.invoke('rbac_management_fixed_2026_04_07', {
      body: { action: 'evaluate_policy', data: { policy_id, context } },
    });
    if (error) throw error;
    return data.data;
  },
};

// Export consolidado
export default {
  iam: iamService,
  pam: pamService,
  billing: billingService,
  rbac: rbacService,
};
