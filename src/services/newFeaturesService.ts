// =====================================================
// KWANZACONTROL - New Features Service
// Serviço para QA, RPA e AI Chat
// Data: 2026-04-08
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// 1. QA & TESTING SERVICE
// =====================================================

export const qaService = {
  // Buscar dashboard de QA
  async getDashboard(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('qa_dashboard_2026_04_08', {
      body: { action: 'get_dashboard', tenantId },
    });

    if (error) throw error;
    return data;
  },

  // Executar teste
  async runTest(tenantId: string, testSuite: string) {
    const { data, error } = await supabase.functions.invoke('qa_dashboard_2026_04_08', {
      body: { action: 'run_test', tenantId, testSuite },
    });

    if (error) throw error;
    return data;
  },

  // Criar bug
  async createBug(tenantId: string, bug: {
    title: string;
    severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
    priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
    description?: string;
  }) {
    const { data, error } = await supabase
      .from('qa_bugs')
      .insert({
        tenant_id: tenantId,
        ...bug,
        status: 'OPEN',
        created_at: new Date().toISOString(),
      })
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Atualizar bug
  async updateBug(bugId: string, updates: {
    status?: 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'CLOSED';
    assigned_to?: string;
  }) {
    const { data, error } = await supabase
      .from('qa_bugs')
      .update(updates)
      .eq('id', bugId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },
};

// =====================================================
// 2. RPA SERVICE
// =====================================================

export const rpaService = {
  // Buscar dashboard de RPA
  async getDashboard(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('rpa_dashboard_2026_04_08', {
      body: { action: 'get_dashboard', tenantId },
    });

    if (error) throw error;
    return data;
  },

  // Executar workflow
  async executeWorkflow(tenantId: string, workflowId: string) {
    const { data, error } = await supabase.functions.invoke('rpa_dashboard_2026_04_08', {
      body: { action: 'execute', tenantId, workflowId },
    });

    if (error) throw error;
    return data;
  },

  // Criar workflow
  async createWorkflow(tenantId: string, workflow: {
    name: string;
    description: string;
    trigger_type: 'MANUAL' | 'SCHEDULED' | 'EVENT';
    schedule?: string;
    steps: any[];
  }) {
    const { data, error } = await supabase
      .from('rpa_workflows')
      .insert({
        tenant_id: tenantId,
        ...workflow,
        enabled: true,
        created_at: new Date().toISOString(),
      })
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Atualizar workflow
  async updateWorkflow(workflowId: string, updates: {
    enabled?: boolean;
    schedule?: string;
    steps?: any[];
  }) {
    const { data, error } = await supabase
      .from('rpa_workflows')
      .update(updates)
      .eq('id', workflowId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Deletar workflow
  async deleteWorkflow(workflowId: string) {
    const { error } = await supabase
      .from('rpa_workflows')
      .delete()
      .eq('id', workflowId);

    if (error) throw error;
  },
};

// =====================================================
// 3. AI CHAT SERVICE
// =====================================================

export const aiChatService = {
  // Listar conversas
  async listConversations(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('ai_chat_2026_04_08', {
      body: { action: 'list_conversations', tenantId },
    });

    if (error) throw error;
    return data.conversations || [];
  },

  // Buscar mensagens de uma conversa
  async getMessages(conversationId: string) {
    const { data, error } = await supabase.functions.invoke('ai_chat_2026_04_08', {
      body: { action: 'get_messages', conversationId },
    });

    if (error) throw error;
    return data.messages || [];
  },

  // Enviar mensagem
  async sendMessage(tenantId: string, conversationId: string | null, message: string) {
    const { data, error } = await supabase.functions.invoke('ai_chat_2026_04_08', {
      body: { action: 'send_message', tenantId, conversationId, message },
    });

    if (error) throw error;
    return data;
  },

  // Nova conversa
  async newConversation(tenantId: string) {
    const { data, error } = await supabase.functions.invoke('ai_chat_2026_04_08', {
      body: { action: 'new_conversation', tenantId },
    });

    if (error) throw error;
    return data.conversation;
  },

  // Deletar conversa
  async deleteConversation(conversationId: string) {
    const { error } = await supabase
      .from('ai_chat_conversations')
      .delete()
      .eq('id', conversationId);

    if (error) throw error;
  },
};

// =====================================================
// TIPOS
// =====================================================

export interface QAMetrics {
  testSuccessRate: number;
  totalTests: number;
  passedTests: number;
  failedTests: number;
  openBugs: number;
  inProgressBugs: number;
  resolvedBugs: number;
  highSeverityBugs: number;
  apiSuccessRate: number;
  apiTestsTotal: number;
  apiTestsPassed: number;
  avgApiResponseTime: number;
  uiSuccessRate: number;
  uiTestsTotal: number;
  uiTestsPassed: number;
  overallCoverage: number;
  qualityScore: number;
}

export interface RPAMetrics {
  totalWorkflows: number;
  activeWorkflows: number;
  inactiveWorkflows: number;
  totalExecutions: number;
  completedExecutions: number;
  failedExecutions: number;
  runningExecutions: number;
  successRate: number;
  avgDuration: number;
  executionsLast24h: number;
  timeSavedHours: number;
  automationScore: number;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface ChatConversation {
  id: string;
  tenant_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}
