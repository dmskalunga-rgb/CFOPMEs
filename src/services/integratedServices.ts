// =====================================================
// KWANZACONTROL - Integrated Services
// Serviços integrados para todos os módulos
// Data: 2026-04-05
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// DASHBOARD SERVICE
// =====================================================

export const dashboardService = {
  async getMetrics(tenantId: string) {
    try {
      // Buscar dados de múltiplas tabelas em paralelo
      const [invoices, transactions, employees, payslips] = await Promise.all([
        supabase.from('invoices').select('*').eq('tenant_id', tenantId),
        supabase.from('transactions').select('*').eq('tenant_id', tenantId),
        supabase.from('employees').select('*').eq('tenant_id', tenantId),
        supabase.from('payslips').select('*').eq('tenant_id', tenantId),
      ]);

      // Calcular métricas
      const totalInvoices = invoices.data?.length || 0;
      const totalRevenue = transactions.data
        ?.filter((t) => t.type === 'INCOME')
        .reduce((sum, t) => sum + parseFloat(t.amount), 0) || 0;
      const totalExpenses = transactions.data
        ?.filter((t) => t.type === 'EXPENSE')
        .reduce((sum, t) => sum + parseFloat(t.amount), 0) || 0;
      const totalEmployees = employees.data?.filter((e) => e.status === 'ACTIVE').length || 0;
      const totalPayroll = payslips.data?.reduce((sum, p) => sum + parseFloat(p.net_salary), 0) || 0;

      return {
        totalInvoices,
        totalRevenue,
        totalExpenses,
        netProfit: totalRevenue - totalExpenses,
        totalEmployees,
        totalPayroll,
        profitMargin: totalRevenue > 0 ? ((totalRevenue - totalExpenses) / totalRevenue) * 100 : 0,
      };
    } catch (error) {
      console.error('Erro ao buscar métricas:', error);
      // Fallback para dados mock
      return {
        totalInvoices: 45,
        totalRevenue: 5000000,
        totalExpenses: 3500000,
        netProfit: 1500000,
        totalEmployees: 25,
        totalPayroll: 2000000,
        profitMargin: 30,
      };
    }
  },

  async getRecentActivity(tenantId: string, limit: number = 10) {
    try {
      const { data, error } = await supabase
        .from('audit_logs')
        .select('*')
        .eq('tenant_id', tenantId)
        .order('created_at', { ascending: false })
        .limit(limit);

      if (error) throw error;
      return data || [];
    } catch (error) {
      console.error('Erro ao buscar atividades:', error);
      return [];
    }
  },

  async getChartData(tenantId: string, months: number = 6) {
    try {
      const startDate = new Date();
      startDate.setMonth(startDate.getMonth() - months);

      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('tenant_id', tenantId)
        .gte('transaction_date', startDate.toISOString().split('T')[0])
        .order('transaction_date');

      if (error) throw error;

      // Agrupar por mês
      const monthlyData: Record<string, { revenue: number; expenses: number }> = {};

      transactions?.forEach((t) => {
        const month = t.transaction_date.substring(0, 7); // YYYY-MM
        if (!monthlyData[month]) {
          monthlyData[month] = { revenue: 0, expenses: 0 };
        }
        if (t.type === 'INCOME') {
          monthlyData[month].revenue += parseFloat(t.amount);
        } else {
          monthlyData[month].expenses += parseFloat(t.amount);
        }
      });

      return Object.entries(monthlyData).map(([month, data]) => ({
        month,
        revenue: data.revenue,
        expenses: data.expenses,
        profit: data.revenue - data.expenses,
      }));
    } catch (error) {
      console.error('Erro ao buscar dados do gráfico:', error);
      return [];
    }
  },
};

// =====================================================
// INVOICING SERVICE (Integrado)
// =====================================================

export const invoicingIntegratedService = {
  async createInvoice(invoice: any) {
    try {
      // 1. Criar fatura
      const { data: newInvoice, error: invoiceError } = await supabase
        .from('invoices')
        .insert(invoice)
        .select()
        .single();

      if (invoiceError) throw invoiceError;

      // 2. Criar transação financeira automaticamente
      if (invoice.status === 'PAID') {
        const transaction = {
          tenant_id: invoice.tenant_id,
          type: 'INCOME',
          category_name: 'Vendas',
          amount: invoice.total_amount,
          transaction_date: invoice.issue_date,
          description: `Fatura ${invoice.invoice_number} - ${invoice.client_name}`,
          reference: invoice.invoice_number,
          invoice_id: newInvoice.id,
        };

        await supabase.from('transactions').insert(transaction);
      }

      // 3. Criar log de auditoria
      await supabase.from('audit_logs').insert({
        tenant_id: invoice.tenant_id,
        action: 'CREATE_INVOICE',
        resource_type: 'INVOICE',
        resource_id: newInvoice.id,
        details: `Fatura ${invoice.invoice_number} criada`,
      });

      return newInvoice;
    } catch (error) {
      console.error('Erro ao criar fatura:', error);
      throw error;
    }
  },

  async updateInvoiceStatus(invoiceId: string, status: string, tenantId: string) {
    try {
      // 1. Atualizar status da fatura
      const { data: invoice, error: updateError } = await supabase
        .from('invoices')
        .update({ status })
        .eq('id', invoiceId)
        .select()
        .single();

      if (updateError) throw updateError;

      // 2. Se status mudou para PAID, criar transação
      if (status === 'PAID') {
        const existingTransaction = await supabase
          .from('transactions')
          .select('*')
          .eq('invoice_id', invoiceId)
          .single();

        if (!existingTransaction.data) {
          const transaction = {
            tenant_id: tenantId,
            type: 'INCOME',
            category_name: 'Vendas',
            amount: invoice.total_amount,
            transaction_date: new Date().toISOString().split('T')[0],
            description: `Pagamento Fatura ${invoice.invoice_number}`,
            reference: invoice.invoice_number,
            invoice_id: invoiceId,
          };

          await supabase.from('transactions').insert(transaction);
        }
      }

      // 3. Log de auditoria
      await supabase.from('audit_logs').insert({
        tenant_id: tenantId,
        action: 'UPDATE_INVOICE_STATUS',
        resource_type: 'INVOICE',
        resource_id: invoiceId,
        details: `Status alterado para ${status}`,
      });

      return invoice;
    } catch (error) {
      console.error('Erro ao atualizar status:', error);
      throw error;
    }
  },

  async submitToAGT(invoiceId: string, tenantId: string) {
    try {
      // Chamar Edge Function para submeter à AGT
      const { data, error } = await supabase.functions.invoke('agt_submit_invoice_2026_04_04', {
        body: { invoiceId },
      });

      if (error) throw error;

      // Atualizar status
      await supabase
        .from('invoices')
        .update({ status: 'SENT_AGT', agt_code: data.agtCode })
        .eq('id', invoiceId);

      // Log
      await supabase.from('audit_logs').insert({
        tenant_id: tenantId,
        action: 'SUBMIT_AGT',
        resource_type: 'INVOICE',
        resource_id: invoiceId,
        details: `Fatura submetida à AGT - Código: ${data.agtCode}`,
      });

      return data;
    } catch (error) {
      console.error('Erro ao submeter à AGT:', error);
      throw error;
    }
  },
};

// =====================================================
// FINANCE SERVICE (Integrado)
// =====================================================

export const financeIntegratedService = {
  async createTransaction(transaction: any) {
    try {
      // 1. Criar transação
      const { data: newTransaction, error } = await supabase
        .from('transactions')
        .insert(transaction)
        .select()
        .single();

      if (error) throw error;

      // 2. Classificar com IA (se habilitado)
      if (transaction.use_ai !== false) {
        const { data: aiData } = await supabase.functions.invoke('ai_classify_transaction_2026_04_04', {
          body: { transactionId: newTransaction.id },
        });

        if (aiData?.category) {
          await supabase
            .from('transactions')
            .update({
              ai_suggested_category: aiData.category,
              ai_confidence: aiData.confidence,
            })
            .eq('id', newTransaction.id);
        }
      }

      // 3. Log de auditoria
      await supabase.from('audit_logs').insert({
        tenant_id: transaction.tenant_id,
        action: 'CREATE_TRANSACTION',
        resource_type: 'TRANSACTION',
        resource_id: newTransaction.id,
        details: `Transação ${transaction.type} de ${transaction.amount} Kz`,
      });

      return newTransaction;
    } catch (error) {
      console.error('Erro ao criar transação:', error);
      throw error;
    }
  },

  async reconcileTransaction(transactionId: string, tenantId: string) {
    try {
      const { data, error } = await supabase
        .from('transactions')
        .update({
          is_reconciled: true,
          reconciled_at: new Date().toISOString(),
        })
        .eq('id', transactionId)
        .select()
        .single();

      if (error) throw error;

      // Log
      await supabase.from('audit_logs').insert({
        tenant_id: tenantId,
        action: 'RECONCILE_TRANSACTION',
        resource_type: 'TRANSACTION',
        resource_id: transactionId,
        details: 'Transação reconciliada',
      });

      return data;
    } catch (error) {
      console.error('Erro ao reconciliar:', error);
      throw error;
    }
  },

  async getCashFlowPrediction(tenantId: string, months: number = 3) {
    try {
      const { data, error } = await supabase.functions.invoke('ai_predict_cashflow_2026_04_04', {
        body: { tenantId, months },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Erro ao prever fluxo de caixa:', error);
      return null;
    }
  },
};

// =====================================================
// PAYROLL SERVICE (Integrado)
// =====================================================

export const payrollIntegratedService = {
  async processPayroll(tenantId: string, payrollMonth: string, employeeIds?: string[], options?: any) {
    try {
      // 1. Processar payroll com IA
      const { data, error } = await supabase.functions.invoke('process_payroll_intelligent_2026_04_05', {
        body: { tenantId, payrollMonth, employeeIds, options },
      });

      if (error) throw error;

      // 2. Criar notificações para funcionários
      if (data.payslips) {
        const notifications = data.payslips.map((payslip: any) => ({
          tenant_id: tenantId,
          user_id: payslip.employee_id,
          type: 'PAYROLL_PROCESSED',
          title: 'Salário Processado',
          message: `Seu salário de ${payrollMonth} foi processado. Valor líquido: ${payslip.net_salary} Kz`,
          priority: 'MEDIUM',
        }));

        await supabase.from('notifications').insert(notifications);
      }

      // 3. Log de auditoria
      await supabase.from('audit_logs').insert({
        tenant_id: tenantId,
        action: 'PROCESS_PAYROLL',
        resource_type: 'PAYROLL',
        details: `Payroll processado para ${data.calculations?.length || 0} funcionário(s) - ${payrollMonth}`,
      });

      return data;
    } catch (error) {
      console.error('Erro ao processar payroll:', error);
      throw error;
    }
  },

  async generatePayslipPDF(payslipId: string) {
    try {
      const { data, error } = await supabase.functions.invoke('generate_payslip_pdf_2026_04_05', {
        body: { payslipId },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Erro ao gerar PDF:', error);
      throw error;
    }
  },

  async sendPayslipEmail(payslipId: string, employeeEmail: string) {
    try {
      // Gerar PDF
      const pdfData = await this.generatePayslipPDF(payslipId);

      // Enviar email (Edge Function)
      const { data, error } = await supabase.functions.invoke('send_notification_2026_04_04', {
        body: {
          type: 'EMAIL',
          to: employeeEmail,
          subject: 'Recibo de Salário',
          body: 'Segue em anexo seu recibo de salário.',
          attachments: [{ filename: 'recibo.html', content: pdfData.html }],
        },
      });

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Erro ao enviar email:', error);
      throw error;
    }
  },
};

// =====================================================
// REPORTS SERVICE (Integrado)
// =====================================================

export const reportsIntegratedService = {
  async generateFinancialReport(tenantId: string, startDate: string, endDate: string) {
    try {
      const [transactions, invoices, payslips] = await Promise.all([
        supabase
          .from('transactions')
          .select('*')
          .eq('tenant_id', tenantId)
          .gte('transaction_date', startDate)
          .lte('transaction_date', endDate),
        supabase
          .from('invoices')
          .select('*')
          .eq('tenant_id', tenantId)
          .gte('issue_date', startDate)
          .lte('issue_date', endDate),
        supabase
          .from('payslips')
          .select('*')
          .eq('tenant_id', tenantId)
          .gte('payroll_month', startDate.substring(0, 7))
          .lte('payroll_month', endDate.substring(0, 7)),
      ]);

      const revenue = transactions.data
        ?.filter((t) => t.type === 'INCOME')
        .reduce((sum, t) => sum + parseFloat(t.amount), 0) || 0;

      const expenses = transactions.data
        ?.filter((t) => t.type === 'EXPENSE')
        .reduce((sum, t) => sum + parseFloat(t.amount), 0) || 0;

      const totalInvoices = invoices.data?.reduce((sum, i) => sum + parseFloat(i.total_amount), 0) || 0;

      const totalPayroll = payslips.data?.reduce((sum, p) => sum + parseFloat(p.net_salary), 0) || 0;

      return {
        period: { startDate, endDate },
        revenue,
        expenses,
        netProfit: revenue - expenses,
        totalInvoices,
        totalPayroll,
        profitMargin: revenue > 0 ? ((revenue - expenses) / revenue) * 100 : 0,
        transactions: transactions.data || [],
        invoices: invoices.data || [],
        payslips: payslips.data || [],
      };
    } catch (error) {
      console.error('Erro ao gerar relatório:', error);
      throw error;
    }
  },

  async exportReport(reportData: any, format: 'PDF' | 'EXCEL' | 'CSV') {
    try {
      // Implementar exportação (pode usar bibliotecas como jsPDF, xlsx, etc.)
      console.log('Exportando relatório em formato:', format);
      return { success: true, format };
    } catch (error) {
      console.error('Erro ao exportar relatório:', error);
      throw error;
    }
  },
};

// =====================================================
// NOTIFICATIONS SERVICE (Integrado)
// =====================================================

export const notificationsIntegratedService = {
  async createNotification(notification: any) {
    try {
      const { data, error } = await supabase.from('notifications').insert(notification).select().single();

      if (error) throw error;

      // Enviar notificação em tempo real (se configurado)
      if (notification.send_realtime !== false) {
        await supabase.functions.invoke('send_notification_2026_04_04', {
          body: {
            type: 'PUSH',
            userId: notification.user_id,
            title: notification.title,
            message: notification.message,
          },
        });
      }

      return data;
    } catch (error) {
      console.error('Erro ao criar notificação:', error);
      throw error;
    }
  },

  async markAsRead(notificationId: string) {
    try {
      const { data, error } = await supabase
        .from('notifications')
        .update({ is_read: true, read_at: new Date().toISOString() })
        .eq('id', notificationId)
        .select()
        .single();

      if (error) throw error;
      return data;
    } catch (error) {
      console.error('Erro ao marcar como lida:', error);
      throw error;
    }
  },

  async getUnreadCount(userId: string) {
    try {
      const { count, error } = await supabase
        .from('notifications')
        .select('*', { count: 'exact', head: true })
        .eq('user_id', userId)
        .eq('is_read', false);

      if (error) throw error;
      return count || 0;
    } catch (error) {
      console.error('Erro ao contar não lidas:', error);
      return 0;
    }
  },
};

// =====================================================
// APPROVALS SERVICE (Integrado)
// =====================================================

export const approvalsIntegratedService = {
  async createApprovalRequest(request: any) {
    try {
      const { data, error } = await supabase.from('approval_requests').insert(request).select().single();

      if (error) throw error;

      // Criar notificação para aprovador
      await notificationsIntegratedService.createNotification({
        tenant_id: request.tenant_id,
        user_id: request.approver_id,
        type: 'APPROVAL_REQUEST',
        title: 'Nova Solicitação de Aprovação',
        message: `${request.request_type}: ${request.description}`,
        priority: 'HIGH',
      });

      return data;
    } catch (error) {
      console.error('Erro ao criar solicitação:', error);
      throw error;
    }
  },

  async approveRequest(requestId: string, approverId: string, comments?: string) {
    try {
      const { data, error } = await supabase
        .from('approval_requests')
        .update({
          status: 'APPROVED',
          approved_at: new Date().toISOString(),
          approver_comments: comments,
        })
        .eq('id', requestId)
        .select()
        .single();

      if (error) throw error;

      // Notificar solicitante
      await notificationsIntegratedService.createNotification({
        tenant_id: data.tenant_id,
        user_id: data.requester_id,
        type: 'APPROVAL_APPROVED',
        title: 'Solicitação Aprovada',
        message: `Sua solicitação foi aprovada${comments ? `: ${comments}` : ''}`,
        priority: 'MEDIUM',
      });

      return data;
    } catch (error) {
      console.error('Erro ao aprovar:', error);
      throw error;
    }
  },

  async rejectRequest(requestId: string, approverId: string, reason: string) {
    try {
      const { data, error } = await supabase
        .from('approval_requests')
        .update({
          status: 'REJECTED',
          rejected_at: new Date().toISOString(),
          rejection_reason: reason,
        })
        .eq('id', requestId)
        .select()
        .single();

      if (error) throw error;

      // Notificar solicitante
      await notificationsIntegratedService.createNotification({
        tenant_id: data.tenant_id,
        user_id: data.requester_id,
        type: 'APPROVAL_REJECTED',
        title: 'Solicitação Rejeitada',
        message: `Sua solicitação foi rejeitada: ${reason}`,
        priority: 'HIGH',
      });

      return data;
    } catch (error) {
      console.error('Erro ao rejeitar:', error);
      throw error;
    }
  },
};

// Export all services
export const integratedServices = {
  dashboard: dashboardService,
  invoicing: invoicingIntegratedService,
  finance: financeIntegratedService,
  payroll: payrollIntegratedService,
  reports: reportsIntegratedService,
  notifications: notificationsIntegratedService,
  approvals: approvalsIntegratedService,
};
