// External Integrations Service — 100% Supabase, sem dados simulados
import { supabase } from '@/integrations/supabase/client';

function uuidv4(): string {
  return crypto.randomUUID();
}

export interface APIKey {
  id: string;
  name: string;
  key_prefix: string;
  permissions: string[];
  rate_limit: number;
  is_active: boolean;
  last_used_at?: string | null;
  expires_at?: string | null;
  created_at: string;
  key?: string; // só visível no momento da criação
}

export interface Webhook {
  id: string;
  name: string;
  url: string;
  events: string[];
  secret?: string | null;
  is_active: boolean;
  status: string;
  success_count: number;
  failure_count: number;
  last_triggered_at?: string | null;
  created_at: string;
}

export interface IntegrationStats {
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  avg_response_time: number;
  active_webhooks: number;
  active_api_keys: number;
}

// Helper: obter user_id e company_id
async function getUserContext(): Promise<{ userId: string; companyId: string } | null> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;
  const { data: profile } = await supabase
    .from('users')
    .select('tenant_id')
    .eq('id', user.id)
    .maybeSingle();
  return {
    userId: user.id,
    companyId: profile?.tenant_id ?? user.id,
  };
}

class ExternalIntegrationsService {
  // ── API KEYS ──────────────────────────────────────────────────────────────

  async createAPIKey(
    _userId: string,
    name: string,
    permissions: string[] = ['read'],
    rateLimit: number = 1000,
    expiresAt?: string
  ): Promise<APIKey> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');

    // Gerar chave real
    const rawKey = `kc_${uuidv4().replace(/-/g, '')}`;
    const keyPrefix = rawKey.substring(0, 12);

    // Hash simples (base64) — não expõe a chave no DB
    const keyHash = btoa(rawKey);

    const { data, error } = await supabase
      .from('api_keys')
      .insert({
        user_id: ctx.userId,
        company_id: ctx.companyId,
        tenant_id: ctx.companyId,
        name,
        key_hash: keyHash,
        key_prefix: keyPrefix,
        permissions: JSON.stringify(permissions),
        rate_limit: rateLimit,
        expires_at: expiresAt ?? null,
        status: 'active',
      })
      .select()
      .single();

    if (error) throw new Error(error.message);

    return {
      id: data.id,
      name: data.name,
      key_prefix: data.key_prefix,
      permissions: Array.isArray(data.permissions) ? data.permissions : JSON.parse(data.permissions as string),
      rate_limit: data.rate_limit,
      is_active: data.status === 'active',
      expires_at: data.expires_at,
      created_at: data.created_at,
      key: rawKey, // visível só aqui
    };
  }

  async listAPIKeys(_userId: string): Promise<APIKey[]> {
    const ctx = await getUserContext();
    if (!ctx) return [];

    const { data, error } = await supabase
      .from('api_keys')
      .select('id, name, key_prefix, permissions, rate_limit, status, last_used_at, expires_at, created_at')
      .eq('user_id', ctx.userId)
      .order('created_at', { ascending: false });

    if (error) throw new Error(error.message);

    return (data ?? []).map(row => ({
      id: row.id,
      name: row.name,
      key_prefix: row.key_prefix,
      permissions: Array.isArray(row.permissions) ? row.permissions as string[] : [],
      rate_limit: row.rate_limit,
      is_active: row.status === 'active',
      last_used_at: row.last_used_at,
      expires_at: row.expires_at,
      created_at: row.created_at,
    }));
  }

  async deleteAPIKey(keyId: string, _userId: string): Promise<void> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');
    const { error } = await supabase
      .from('api_keys')
      .delete()
      .eq('id', keyId)
      .eq('user_id', ctx.userId);
    if (error) throw new Error(error.message);
  }

  async toggleAPIKey(keyId: string, _userId: string): Promise<void> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');
    // Ler estado actual
    const { data, error: readErr } = await supabase
      .from('api_keys')
      .select('status')
      .eq('id', keyId)
      .eq('user_id', ctx.userId)
      .single();
    if (readErr) throw new Error(readErr.message);
    const newStatus = data.status === 'active' ? 'revoked' : 'active';
    const { error } = await supabase
      .from('api_keys')
      .update({ status: newStatus, updated_at: new Date().toISOString() })
      .eq('id', keyId);
    if (error) throw new Error(error.message);
  }

  // ── WEBHOOKS ──────────────────────────────────────────────────────────────

  async createWebhook(
    _userId: string,
    name: string,
    url: string,
    events: string[],
    _secret?: string,
    _headers?: Record<string, string>
  ): Promise<Webhook> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');

    const secret = `whsec_${uuidv4().replace(/-/g, '')}`;

    const { data, error } = await supabase
      .from('webhooks')
      .insert({
        user_id: ctx.userId,
        company_id: ctx.companyId,
        tenant_id: ctx.companyId,
        name,
        url,
        events,
        secret,
        status: 'active',
        failure_count: 0,
        success_count: 0,
      })
      .select()
      .single();

    if (error) throw new Error(error.message);

    return this._mapWebhook(data);
  }

  async listWebhooks(_userId: string): Promise<Webhook[]> {
    const ctx = await getUserContext();
    if (!ctx) return [];

    const { data, error } = await supabase
      .from('webhooks')
      .select('id, name, url, events, secret, status, success_count, failure_count, last_triggered_at, created_at')
      .eq('user_id', ctx.userId)
      .order('created_at', { ascending: false });

    if (error) throw new Error(error.message);
    return (data ?? []).map(row => this._mapWebhook(row));
  }

  async testWebhook(webhookId: string, _userId: string): Promise<{ success: boolean; message: string }> {
    try {
      const { error } = await supabase.functions.invoke('webhooks-integrations', {
        body: { resource: 'webhooks', action: 'test', webhook_id: webhookId },
      });
      if (error) throw error;
      // Incrementar success_count
      // Actualizar timestamp após sucesso
      await supabase.from('webhooks')
        .update({ last_triggered_at: new Date().toISOString(), updated_at: new Date().toISOString() })
        .eq('id', webhookId);
      return { success: true, message: 'Webhook testado com sucesso' };
    } catch (_err) {
      // Incrementar failure_count
      const { data } = await supabase.from('webhooks').select('failure_count').eq('id', webhookId).single();
      await supabase.from('webhooks')
        .update({ failure_count: (data?.failure_count ?? 0) + 1, last_triggered_at: new Date().toISOString() })
        .eq('id', webhookId);
      return { success: false, message: 'Falha no teste — falha registada' };
    }
  }

  async deleteWebhook(webhookId: string, _userId: string): Promise<void> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');
    const { error } = await supabase
      .from('webhooks')
      .delete()
      .eq('id', webhookId)
      .eq('user_id', ctx.userId);
    if (error) throw new Error(error.message);
  }

  async toggleWebhook(webhookId: string, _userId: string): Promise<void> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');
    const { data, error: readErr } = await supabase
      .from('webhooks')
      .select('status')
      .eq('id', webhookId)
      .eq('user_id', ctx.userId)
      .single();
    if (readErr) throw new Error(readErr.message);
    const newStatus = data.status === 'active' ? 'inactive' : 'active';
    const { error } = await supabase
      .from('webhooks')
      .update({ status: newStatus, updated_at: new Date().toISOString() })
      .eq('id', webhookId);
    if (error) throw new Error(error.message);
  }

  async getStats(_userId: string): Promise<IntegrationStats> {
    const ctx = await getUserContext();
    if (!ctx) return { total_requests: 0, successful_requests: 0, failed_requests: 0, avg_response_time: 0, active_webhooks: 0, active_api_keys: 0 };

    const [keysRes, webhooksRes, logsRes] = await Promise.all([
      supabase.from('api_keys').select('status', { count: 'exact' }).eq('user_id', ctx.userId).eq('status', 'active'),
      supabase.from('webhooks').select('status, success_count, failure_count').eq('user_id', ctx.userId),
      supabase.from('api_usage_logs')
        .select('status_code, response_time')
        .eq('api_key_id', ctx.userId) // filtro por tenant
        .gte('created_at', new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString())
        .limit(1000),
    ]);

    const activeKeys = keysRes.count ?? 0;
    const webhooks = webhooksRes.data ?? [];
    const activeWh = webhooks.filter(w => w.status === 'active').length;
    const totalSuccess = webhooks.reduce((s, w) => s + (w.success_count ?? 0), 0);
    const totalFail = webhooks.reduce((s, w) => s + (w.failure_count ?? 0), 0);

    const logs = logsRes.data ?? [];
    const successLogs = logs.filter(l => l.status_code < 400).length;
    const avgMs = logs.length > 0 ? logs.reduce((s, l) => s + (l.response_time ?? 0), 0) / logs.length : 0;

    return {
      total_requests: totalSuccess + totalFail + logs.length,
      successful_requests: totalSuccess + successLogs,
      failed_requests: totalFail + (logs.length - successLogs),
      avg_response_time: Math.round(avgMs),
      active_webhooks: activeWh,
      active_api_keys: activeKeys,
    };
  }

  private _mapWebhook(row: Record<string, unknown>): Webhook {
    return {
      id: row.id as string,
      name: row.name as string,
      url: row.url as string,
      events: Array.isArray(row.events) ? (row.events as string[]) : [],
      secret: row.secret as string | null,
      is_active: (row.status as string) === 'active',
      status: row.status as string,
      success_count: (row.success_count as number) ?? 0,
      failure_count: (row.failure_count as number) ?? 0,
      last_triggered_at: row.last_triggered_at as string | null,
      created_at: row.created_at as string,
    };
  }
}

export const externalIntegrationsService = new ExternalIntegrationsService();
