// Notifications Service - Versão com dados de demonstração
import { supabase } from '@/integrations/supabase/client';

export interface EmailTemplate {
  id: string;
  name: string;
  subject: string;
  html_content: string;
  text_content?: string;
  variables: string[];
  category: string;
  is_active: boolean;
}

export interface EmailLog {
  id: string;
  to_email: string;
  subject: string;
  status: string;
  provider_id?: string;
  sent_at: string;
  error_message?: string;
}

export interface NotificationPreferences {
  email_enabled: boolean;
  push_enabled: boolean;
  sms_enabled: boolean;
  categories: {
    system: boolean;
    billing: boolean;
    security: boolean;
    marketing: boolean;
    updates: boolean;
  };
  quiet_hours: {
    enabled: boolean;
    start: string;
    end: string;
  };
}

export interface NotificationStats {
  pending: number;
  sent: number;
  failed: number;
}

// Dados de demonstração
const mockTemplates: EmailTemplate[] = [
  {
    id: '1',
    name: 'welcome_email',
    subject: 'Bem-vindo ao KWANZACONTROL',
    html_content: '<h1>Olá {{name}}</h1><p>Bem-vindo ao nosso sistema!</p>',
    text_content: 'Olá {{name}}, Bem-vindo ao nosso sistema!',
    variables: ['name', 'email'],
    category: 'system',
    is_active: true
  },
  {
    id: '2',
    name: 'invoice_created',
    subject: 'Nova Fatura #{{invoice_number}}',
    html_content: '<h1>Fatura Criada</h1><p>Fatura #{{invoice_number}} no valor de {{amount}}</p>',
    text_content: 'Fatura #{{invoice_number}} criada no valor de {{amount}}',
    variables: ['invoice_number', 'amount', 'customer_name'],
    category: 'billing',
    is_active: true
  },
  {
    id: '3',
    name: 'password_reset',
    subject: 'Redefinição de Senha',
    html_content: '<h1>Redefinir Senha</h1><p>Clique no link: {{reset_link}}</p>',
    text_content: 'Redefinir senha: {{reset_link}}',
    variables: ['reset_link', 'user_email'],
    category: 'security',
    is_active: true
  },
  {
    id: '4',
    name: 'payment_received',
    subject: 'Pagamento Recebido',
    html_content: '<h1>Pagamento Confirmado</h1><p>Recebemos seu pagamento de {{amount}}</p>',
    text_content: 'Pagamento de {{amount}} confirmado',
    variables: ['amount', 'payment_method', 'transaction_id'],
    category: 'billing',
    is_active: true
  }
];

let mockLogs: EmailLog[] = [
  {
    id: '1',
    to_email: 'cliente@example.com',
    subject: 'Bem-vindo ao KWANZACONTROL',
    status: 'sent',
    provider_id: 'msg_123456',
    sent_at: new Date(Date.now() - 5 * 60 * 1000).toISOString()
  },
  {
    id: '2',
    to_email: 'empresa@example.com',
    subject: 'Nova Fatura #INV-2026-00145',
    status: 'sent',
    provider_id: 'msg_123457',
    sent_at: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString()
  },
  {
    id: '3',
    to_email: 'usuario@example.com',
    subject: 'Redefinição de Senha',
    status: 'sent',
    provider_id: 'msg_123458',
    sent_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
  },
  {
    id: '4',
    to_email: 'teste@example.com',
    subject: 'Email de Teste',
    status: 'failed',
    sent_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    error_message: 'Email inválido'
  },
  {
    id: '5',
    to_email: 'pagamento@example.com',
    subject: 'Pagamento Recebido',
    status: 'sent',
    provider_id: 'msg_123459',
    sent_at: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString()
  }
];

let mockPreferences: NotificationPreferences = {
  email_enabled: true,
  push_enabled: true,
  sms_enabled: false,
  categories: {
    system: true,
    billing: true,
    security: true,
    marketing: false,
    updates: true
  },
  quiet_hours: {
    enabled: true,
    start: '22:00',
    end: '08:00'
  }
};

class NotificationsService {
  async sendEmail(data: {
    to: string | string[];
    subject: string;
    html?: string;
    text?: string;
    template?: string;
    variables?: Record<string, any>;
    priority?: number;
    scheduledAt?: string;
  }) {
    // Simular envio de email
    const recipients = Array.isArray(data.to) ? data.to : [data.to];
    
    recipients.forEach(email => {
      const newLog: EmailLog = {
        id: String(mockLogs.length + 1),
        to_email: email,
        subject: data.subject,
        status: 'sent',
        provider_id: `msg_${Math.random().toString(36).substring(7)}`,
        sent_at: new Date().toISOString()
      };
      mockLogs.unshift(newLog);
    });

    return { success: true, sent: recipients.length };
  }

  async sendTemplateEmail(
    to: string | string[],
    templateName: string,
    variables: Record<string, any>
  ) {
    const template = mockTemplates.find(t => t.name === templateName);
    if (!template) throw new Error('Template não encontrado');

    return this.sendEmail({
      to,
      subject: template.subject,
      template: templateName,
      variables
    });
  }

  async processQueue() {
    // Simular processamento de fila
    const processed = Math.floor(Math.random() * 10) + 1;
    return { success: true, processed };
  }

  async getTemplates(): Promise<EmailTemplate[]> {
    return mockTemplates;
  }

  async getLogs(limit = 50, offset = 0): Promise<{ logs: EmailLog[]; total: number }> {
    const logs = mockLogs.slice(offset, offset + limit);
    return { logs, total: mockLogs.length };
  }

  async getPreferences(userId: string): Promise<NotificationPreferences | null> {
    return mockPreferences;
  }

  async updatePreferences(
    userId: string,
    preferences: Partial<NotificationPreferences>
  ): Promise<NotificationPreferences> {
    mockPreferences = { ...mockPreferences, ...preferences };
    return mockPreferences;
  }

  async getStats(): Promise<NotificationStats> {
    const sent = mockLogs.filter(l => l.status === 'sent').length;
    const failed = mockLogs.filter(l => l.status === 'failed').length;
    const pending = Math.floor(Math.random() * 5);

    return {
      pending,
      sent,
      failed
    };
  }

  async createTemplate(template: Omit<EmailTemplate, 'id'>): Promise<EmailTemplate> {
    const newTemplate: EmailTemplate = {
      ...template,
      id: String(mockTemplates.length + 1)
    };
    mockTemplates.push(newTemplate);
    return newTemplate;
  }

  async updateTemplate(id: string, updates: Partial<EmailTemplate>): Promise<EmailTemplate> {
    const index = mockTemplates.findIndex(t => t.id === id);
    if (index === -1) throw new Error('Template não encontrado');
    
    mockTemplates[index] = { ...mockTemplates[index], ...updates };
    return mockTemplates[index];
  }

  async deleteTemplate(id: string): Promise<void> {
    const index = mockTemplates.findIndex(t => t.id === id);
    if (index !== -1) {
      mockTemplates.splice(index, 1);
    }
  }
}

export const notificationsService = new NotificationsService();
