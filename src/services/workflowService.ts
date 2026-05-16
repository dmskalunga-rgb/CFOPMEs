// =====================================================
// KWANZACONTROL - Workflow Automation Service
// Sistema de automação de processos e workflows
// =====================================================

import { supabase } from '@/integrations/supabase/client';
import { notificationService } from './notificationService';

export interface Workflow {
  id: string;
  organization_id: string;
  name: string;
  description: string;
  trigger_type: 'manual' | 'scheduled' | 'event';
  trigger_config: Record<string, any>;
  steps: WorkflowStep[];
  enabled: boolean;
  created_at: string;
}

export interface WorkflowStep {
  id: string;
  type: 'condition' | 'action' | 'approval' | 'notification' | 'delay';
  config: Record<string, any>;
  next_step_id?: string;
  next_step_on_fail?: string;
}

export interface ApprovalRequest {
  id: string;
  workflow_id: string;
  organization_id: string;
  requester_id: string;
  approver_id: string;
  type: 'invoice' | 'expense' | 'budget' | 'payment';
  entity_id: string;
  amount?: number;
  status: 'pending' | 'approved' | 'rejected';
  comments?: string;
  created_at: string;
  resolved_at?: string;
}

class WorkflowService {
  // Criar workflow
  async createWorkflow(workflow: Omit<Workflow, 'id' | 'created_at'>): Promise<Workflow> {
    const { data, error } = await supabase
      .from('workflows')
      .insert(workflow)
      .select()
      .single();

    if (error) throw error;
    return data;
  }

  // Executar workflow
  async executeWorkflow(workflowId: string, context: Record<string, any>): Promise<void> {
    const { data: workflow, error } = await supabase
      .from('workflows')
      .select('*')
      .eq('id', workflowId)
      .single();

    if (error) throw error;
    if (!workflow.enabled) throw new Error('Workflow is disabled');

    // Executar steps sequencialmente
    let currentStep = workflow.steps[0];
    let stepContext = { ...context };

    while (currentStep) {
      try {
        const result = await this.executeStep(currentStep, stepContext);
        stepContext = { ...stepContext, ...result };

        // Próximo step
        if (result.success && currentStep.next_step_id) {
          currentStep = workflow.steps.find((s: WorkflowStep) => s.id === currentStep.next_step_id);
        } else if (!result.success && currentStep.next_step_on_fail) {
          currentStep = workflow.steps.find((s: WorkflowStep) => s.id === currentStep.next_step_on_fail);
        } else {
          break;
        }
      } catch (error) {
        console.error('Error executing workflow step:', error);
        break;
      }
    }
  }

  // Executar step individual
  private async executeStep(step: WorkflowStep, context: Record<string, any>): Promise<any> {
    switch (step.type) {
      case 'condition':
        return this.executeCondition(step, context);
      case 'action':
        return this.executeAction(step, context);
      case 'approval':
        return this.executeApproval(step, context);
      case 'notification':
        return this.executeNotification(step, context);
      case 'delay':
        return this.executeDelay(step, context);
      default:
        return { success: true };
    }
  }

  // Executar condição
  private async executeCondition(step: WorkflowStep, context: Record<string, any>): Promise<any> {
    const { field, operator, value } = step.config;
    const fieldValue = context[field];

    let result = false;

    switch (operator) {
      case 'equals':
        result = fieldValue === value;
        break;
      case 'greater_than':
        result = fieldValue > value;
        break;
      case 'less_than':
        result = fieldValue < value;
        break;
      case 'contains':
        result = String(fieldValue).includes(value);
        break;
      default:
        result = false;
    }

    return { success: result };
  }

  // Executar ação
  private async executeAction(step: WorkflowStep, context: Record<string, any>): Promise<any> {
    const { action_type, params } = step.config;

    switch (action_type) {
      case 'update_status':
        await this.updateEntityStatus(params.entity_type, params.entity_id, params.new_status);
        break;
      case 'send_email':
        await this.sendEmail(params.to, params.subject, params.body);
        break;
      case 'create_task':
        await this.createTask(params.title, params.description, params.assignee_id);
        break;
      case 'generate_report':
        await this.generateReport(params.report_type, params.filters);
        break;
      default:
        console.log('Unknown action type:', action_type);
    }

    return { success: true };
  }

  // Executar aprovação
  private async executeApproval(step: WorkflowStep, context: Record<string, any>): Promise<any> {
    const { approver_id, type, entity_id, amount } = step.config;

    const approval = await this.createApprovalRequest({
      workflow_id: context.workflow_id,
      organization_id: context.organization_id,
      requester_id: context.user_id,
      approver_id,
      type,
      entity_id,
      amount,
      status: 'pending',
    });

    // Notificar aprovador
    await notificationService.createNotification({
      user_id: approver_id,
      organization_id: context.organization_id,
      type: 'info',
      category: 'approval',
      title: 'Nova Solicitação de Aprovação',
      message: `Você tem uma nova solicitação de aprovação para ${type}.`,
      action_url: `/approvals?id=${approval.id}`,
      action_label: 'Ver Solicitação',
    });

    return { success: true, approval_id: approval.id };
  }

  // Executar notificação
  private async executeNotification(step: WorkflowStep, context: Record<string, any>): Promise<any> {
    const { user_id, title, message, action_url } = step.config;

    await notificationService.createNotification({
      user_id: user_id || context.user_id,
      organization_id: context.organization_id,
      type: 'info',
      category: 'system',
      title,
      message,
      action_url,
    });

    return { success: true };
  }

  // Executar delay
  private async executeDelay(step: WorkflowStep, context: Record<string, any>): Promise<any> {
    const { duration_ms } = step.config;
    await new Promise(resolve => setTimeout(resolve, duration_ms));
    return { success: true };
  }

  // Criar solicitação de aprovação
  async createApprovalRequest(approval: Omit<ApprovalRequest, 'id' | 'created_at'>): Promise<ApprovalRequest> {
    const { data, error } = await supabase
      .from('approval_requests')
      .insert(approval)
      .select()
      .single();

    if (error) throw error;
    return data;
  }

  // Aprovar/Rejeitar solicitação
  async resolveApproval(approvalId: string, status: 'approved' | 'rejected', comments?: string): Promise<void> {
    const { error } = await supabase
      .from('approval_requests')
      .update({
        status,
        comments,
        resolved_at: new Date().toISOString(),
      })
      .eq('id', approvalId);

    if (error) throw error;

    // Notificar solicitante
    const { data: approval } = await supabase
      .from('approval_requests')
      .select('*')
      .eq('id', approvalId)
      .single();

    if (approval) {
      await notificationService.createNotification({
        user_id: approval.requester_id,
        organization_id: approval.organization_id,
        type: status === 'approved' ? 'success' : 'error',
        category: 'approval',
        title: status === 'approved' ? 'Solicitação Aprovada' : 'Solicitação Rejeitada',
        message: `Sua solicitação de ${approval.type} foi ${status === 'approved' ? 'aprovada' : 'rejeitada'}.`,
      });
    }
  }

  // Listar aprovações pendentes
  async getPendingApprovals(approverId: string): Promise<ApprovalRequest[]> {
    const { data, error } = await supabase
      .from('approval_requests')
      .select('*')
      .eq('approver_id', approverId)
      .eq('status', 'pending')
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data || [];
  }

  // WORKFLOWS PRÉ-DEFINIDOS

  // Workflow: Aprovação automática de despesas pequenas
  async createAutoApprovalWorkflow(organizationId: string, threshold: number): Promise<Workflow> {
    return this.createWorkflow({
      organization_id: organizationId,
      name: 'Aprovação Automática de Despesas',
      description: `Aprova automaticamente despesas abaixo de ${threshold} Kz`,
      trigger_type: 'event',
      trigger_config: { event: 'expense_created' },
      enabled: true,
      steps: [
        {
          id: 'check-amount',
          type: 'condition',
          config: {
            field: 'amount',
            operator: 'less_than',
            value: threshold,
          },
          next_step_id: 'auto-approve',
          next_step_on_fail: 'request-approval',
        },
        {
          id: 'auto-approve',
          type: 'action',
          config: {
            action_type: 'update_status',
            params: {
              entity_type: 'expense',
              entity_id: '{{entity_id}}',
              new_status: 'approved',
            },
          },
        },
        {
          id: 'request-approval',
          type: 'approval',
          config: {
            approver_id: '{{manager_id}}',
            type: 'expense',
            entity_id: '{{entity_id}}',
            amount: '{{amount}}',
          },
        },
      ],
    });
  }

  // Workflow: Lembrete de faturas vencendo
  async createInvoiceReminderWorkflow(organizationId: string): Promise<Workflow> {
    return this.createWorkflow({
      organization_id: organizationId,
      name: 'Lembrete de Faturas Vencendo',
      description: 'Envia lembretes automáticos de faturas vencendo',
      trigger_type: 'scheduled',
      trigger_config: { cron: '0 9 * * *' }, // Diariamente às 9h
      enabled: true,
      steps: [
        {
          id: 'check-invoices',
          type: 'action',
          config: {
            action_type: 'check_due_invoices',
            params: { days_before: 3 },
          },
          next_step_id: 'send-notification',
        },
        {
          id: 'send-notification',
          type: 'notification',
          config: {
            title: 'Faturas Vencendo',
            message: 'Você tem faturas vencendo nos próximos 3 dias.',
            action_url: '/invoicing',
          },
        },
      ],
    });
  }

  // Helpers privados
  private async updateEntityStatus(entityType: string, entityId: string, newStatus: string): Promise<void> {
    const table = `${entityType}s`;
    await supabase.from(table).update({ status: newStatus }).eq('id', entityId);
  }

  private async sendEmail(to: string, subject: string, body: string): Promise<void> {
    // Implementar integração com serviço de email (Resend, etc)
    console.log('Sending email:', { to, subject, body });
  }

  private async createTask(title: string, description: string, assigneeId: string): Promise<void> {
    await supabase.from('tasks').insert({
      title,
      description,
      assignee_id: assigneeId,
      status: 'pending',
    });
  }

  private async generateReport(reportType: string, filters: Record<string, any>): Promise<void> {
    // Implementar geração de relatório
    console.log('Generating report:', { reportType, filters });
  }
}

export const workflowService = new WorkflowService();
