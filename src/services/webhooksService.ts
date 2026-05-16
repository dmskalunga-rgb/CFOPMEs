import { supabase } from '@/integrations/supabase/client';

export interface Webhook {
  id: string;
  tenant_id: string;
  name: string;
  url: string;
  events: string[];
  secret: string;
  status: 'active' | 'inactive' | 'failed';
  last_triggered_at: string | null;
  failure_count: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateWebhookInput {
  name: string;
  url: string;
  events: string[];
}

export interface WebhookLog {
  id: string;
  webhook_id: string;
  event: string;
  payload: any;
  response_status: number | null;
  response_body: string | null;
  error: string | null;
  created_at: string;
}

class WebhooksService {
  async listWebhooks(tenantId: string): Promise<Webhook[]> {
    try {
      const { data, error } = await supabase
        .from('webhooks')
        .select('*')
        .eq('tenant_id', tenantId)
        .order('created_at', { ascending: false });

      if (error) {
        console.error('Erro ao listar Webhooks:', error);
        // Se a tabela não existe, retornar array vazio
        if (error.code === '42P01' || error.message.includes('does not exist')) {
          return [];
        }
        throw error;
      }
      
      return data || [];
    } catch (error: any) {
      console.error('Erro ao listar Webhooks:', error);
      // Retornar array vazio em caso de erro
      return [];
    }
  }

  async createWebhook(tenantId: string, input: CreateWebhookInput): Promise<Webhook> {
    try {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) throw new Error('Usuário não autenticado');

      // Generate webhook secret
      const secret = this.generateSecret();

      const { data, error } = await supabase
        .from('webhooks')
        .insert({
          tenant_id: tenantId,
          name: input.name,
          url: input.url,
          events: input.events,
          secret,
          status: 'active',
          failure_count: 0,
          created_by: user.id,
        })
        .select()
        .single();

      if (error) {
        console.error('Erro ao criar Webhook:', error);
        throw new Error(`Erro ao criar Webhook: ${error.message}`);
      }
      
      return data;
    } catch (error: any) {
      console.error('Erro ao criar Webhook:', error);
      throw error;
    }
  }

  async updateWebhook(id: string, input: Partial<CreateWebhookInput>): Promise<Webhook> {
    try {
      const { data, error } = await supabase
        .from('webhooks')
        .update(input)
        .eq('id', id)
        .select()
        .single();

      if (error) {
        console.error('Erro ao atualizar Webhook:', error);
        throw new Error(`Erro ao atualizar Webhook: ${error.message}`);
      }
      
      return data;
    } catch (error: any) {
      console.error('Erro ao atualizar Webhook:', error);
      throw error;
    }
  }

  async deleteWebhook(id: string): Promise<void> {
    try {
      const { error } = await supabase
        .from('webhooks')
        .delete()
        .eq('id', id);

      if (error) {
        console.error('Erro ao deletar Webhook:', error);
        throw new Error(`Erro ao deletar Webhook: ${error.message}`);
      }
    } catch (error: any) {
      console.error('Erro ao deletar Webhook:', error);
      throw error;
    }
  }

  async toggleWebhook(id: string, status: 'active' | 'inactive'): Promise<void> {
    try {
      const { error } = await supabase
        .from('webhooks')
        .update({ status })
        .eq('id', id);

      if (error) {
        console.error('Erro ao alternar status do Webhook:', error);
        throw new Error(`Erro ao alternar status do Webhook: ${error.message}`);
      }
    } catch (error: any) {
      console.error('Erro ao alternar status do Webhook:', error);
      throw error;
    }
  }

  async getWebhookLogs(webhookId: string, limit: number = 50): Promise<WebhookLog[]> {
    try {
      const { data, error } = await supabase
        .from('webhook_logs')
        .select('*')
        .eq('webhook_id', webhookId)
        .order('created_at', { ascending: false })
        .limit(limit);

      if (error) {
        console.error('Erro ao buscar logs do Webhook:', error);
        // Se a tabela não existe, retornar array vazio
        if (error.code === '42P01' || error.message.includes('does not exist')) {
          return [];
        }
        throw error;
      }
      
      return data || [];
    } catch (error: any) {
      console.error('Erro ao buscar logs do Webhook:', error);
      return [];
    }
  }

  async testWebhook(webhookId: string): Promise<{ success: boolean; message: string }> {
    try {
      const webhook = await this.getWebhook(webhookId);
      
      const response = await fetch(webhook.url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Webhook-Signature': this.generateSignature(webhook.secret, { test: true }),
        },
        body: JSON.stringify({
          event: 'webhook.test',
          data: { test: true },
          timestamp: new Date().toISOString(),
        }),
      });

      return {
        success: response.ok,
        message: response.ok ? 'Webhook testado com sucesso' : `Falhou com status ${response.status}`,
      };
    } catch (error: any) {
      console.error('Erro ao testar Webhook:', error);
      return {
        success: false,
        message: error.message || 'Erro desconhecido',
      };
    }
  }

  private async getWebhook(id: string): Promise<Webhook> {
    const { data, error } = await supabase
      .from('webhooks')
      .select('*')
      .eq('id', id)
      .single();

    if (error) {
      console.error('Erro ao buscar Webhook:', error);
      throw new Error(`Erro ao buscar Webhook: ${error.message}`);
    }
    
    return data;
  }

  private generateSecret(): string {
    const array = new Uint8Array(32);
    crypto.getRandomValues(array);
    return Array.from(array, byte => byte.toString(16).padStart(2, '0')).join('');
  }

  private generateSignature(secret: string, payload: any): string {
    // Simple signature for demo - in production use HMAC
    return btoa(secret + JSON.stringify(payload));
  }

  getAvailableEvents(): string[] {
    return [
      'transaction.created',
      'transaction.updated',
      'transaction.deleted',
      'invoice.created',
      'invoice.paid',
      'invoice.overdue',
      'budget.exceeded',
      'report.generated',
      'user.created',
      'user.updated',
    ];
  }
}

export const webhooksService = new WebhooksService();
