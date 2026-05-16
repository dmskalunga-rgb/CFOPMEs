/**
 * Contract Management Service
 * Serviço de gestão de contratos e compromissos financeiros
 */

import { supabase } from '@/integrations/supabase/client';

export interface Contract {
  id: string;
  companyId: string;
  type: 'supplier' | 'customer' | 'employee' | 'service' | 'lease' | 'other';
  name: string;
  counterparty: string;
  description: string;
  value: number;
  currency: string;
  startDate: Date;
  endDate: Date;
  renewalType: 'manual' | 'automatic' | 'none';
  renewalNoticeDays: number;
  paymentFrequency: 'monthly' | 'quarterly' | 'yearly' | 'one_time';
  nextPaymentDate?: Date;
  status: 'draft' | 'active' | 'expired' | 'cancelled' | 'renewed';
  documents: ContractDocument[];
  milestones: ContractMilestone[];
  alerts: ContractAlert[];
  createdAt: Date;
  updatedAt: Date;
}

export interface ContractDocument {
  id: string;
  name: string;
  type: string;
  url: string;
  uploadedAt: Date;
}

export interface ContractMilestone {
  id: string;
  name: string;
  date: Date;
  amount?: number;
  status: 'pending' | 'completed' | 'overdue';
  notes?: string;
}

export interface ContractAlert {
  id: string;
  type: 'renewal' | 'payment' | 'expiration' | 'milestone';
  severity: 'info' | 'warning' | 'critical';
  message: string;
  date: Date;
  dismissed: boolean;
}

export interface ContractSummary {
  totalContracts: number;
  activeContracts: number;
  expiringContracts: number;
  totalValue: number;
  monthlyCommitment: number;
  upcomingPayments: Array<{
    contractId: string;
    contractName: string;
    amount: number;
    dueDate: Date;
  }>;
}

class ContractManagementService {
  /**
   * Criar novo contrato
   */
  async createContract(contract: Omit<Contract, 'id' | 'createdAt' | 'updatedAt'>): Promise<Contract> {
    try {
      const { data, error } = await supabase
        .from('contracts')
        .insert({
          company_id: contract.companyId,
          type: contract.type,
          name: contract.name,
          counterparty: contract.counterparty,
          description: contract.description,
          value: contract.value,
          currency: contract.currency,
          start_date: contract.startDate,
          end_date: contract.endDate,
          renewal_type: contract.renewalType,
          renewal_notice_days: contract.renewalNoticeDays,
          payment_frequency: contract.paymentFrequency,
          next_payment_date: contract.nextPaymentDate,
          status: contract.status,
          documents: contract.documents,
          milestones: contract.milestones,
          alerts: contract.alerts,
        })
        .select()
        .single();

      if (error) throw error;

      return this.mapContract(data);
    } catch (error) {
      console.error('Erro ao criar contrato:', error);
      throw error;
    }
  }

  /**
   * Listar contratos
   */
  async listContracts(companyId: string, filters?: {
    type?: string;
    status?: string;
  }): Promise<Contract[]> {
    try {
      let query = supabase
        .from('contracts')
        .select('*')
        .eq('company_id', companyId);

      if (filters?.type) {
        query = query.eq('type', filters.type);
      }

      if (filters?.status) {
        query = query.eq('status', filters.status);
      }

      const { data, error } = await query.order('created_at', { ascending: false });

      if (error) throw error;

      return (data || []).map(this.mapContract);
    } catch (error) {
      console.error('Erro ao listar contratos:', error);
      return this.getMockContracts();
    }
  }

  /**
   * Obter contrato por ID
   */
  async getContract(contractId: string): Promise<Contract | null> {
    try {
      const { data, error } = await supabase
        .from('contracts')
        .select('*')
        .eq('id', contractId)
        .single();

      if (error) throw error;

      return this.mapContract(data);
    } catch (error) {
      console.error('Erro ao obter contrato:', error);
      return null;
    }
  }

  /**
   * Atualizar contrato
   */
  async updateContract(contractId: string, updates: Partial<Contract>): Promise<Contract> {
    try {
      const { data, error } = await supabase
        .from('contracts')
        .update({
          name: updates.name,
          description: updates.description,
          value: updates.value,
          end_date: updates.endDate,
          status: updates.status,
          documents: updates.documents,
          milestones: updates.milestones,
        })
        .eq('id', contractId)
        .select()
        .single();

      if (error) throw error;

      return this.mapContract(data);
    } catch (error) {
      console.error('Erro ao atualizar contrato:', error);
      throw error;
    }
  }

  /**
   * Obter resumo de contratos
   */
  async getContractSummary(companyId: string): Promise<ContractSummary> {
    try {
      const contracts = await this.listContracts(companyId);

      const activeContracts = contracts.filter((c) => c.status === 'active');
      const now = new Date();
      const in30Days = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);

      const expiringContracts = activeContracts.filter(
        (c) => c.endDate >= now && c.endDate <= in30Days
      );

      const totalValue = activeContracts.reduce((sum, c) => sum + c.value, 0);

      // Calcular compromisso mensal
      const monthlyCommitment = activeContracts.reduce((sum, c) => {
        if (c.paymentFrequency === 'monthly') return sum + c.value;
        if (c.paymentFrequency === 'quarterly') return sum + c.value / 3;
        if (c.paymentFrequency === 'yearly') return sum + c.value / 12;
        return sum;
      }, 0);

      // Próximos pagamentos
      const upcomingPayments = activeContracts
        .filter((c) => c.nextPaymentDate && c.nextPaymentDate <= in30Days)
        .map((c) => ({
          contractId: c.id,
          contractName: c.name,
          amount: this.calculatePaymentAmount(c),
          dueDate: c.nextPaymentDate!,
        }))
        .sort((a, b) => a.dueDate.getTime() - b.dueDate.getTime());

      return {
        totalContracts: contracts.length,
        activeContracts: activeContracts.length,
        expiringContracts: expiringContracts.length,
        totalValue,
        monthlyCommitment,
        upcomingPayments,
      };
    } catch (error) {
      console.error('Erro ao obter resumo:', error);
      throw error;
    }
  }

  /**
   * Verificar alertas de contratos
   */
  async checkContractAlerts(companyId: string): Promise<ContractAlert[]> {
    try {
      const contracts = await this.listContracts(companyId, { status: 'active' });
      const alerts: ContractAlert[] = [];
      const now = new Date();

      contracts.forEach((contract) => {
        // Alerta de renovação
        if (contract.renewalType !== 'none') {
          const renewalDate = new Date(contract.endDate);
          renewalDate.setDate(renewalDate.getDate() - contract.renewalNoticeDays);

          if (now >= renewalDate && now <= contract.endDate) {
            alerts.push({
              id: `alert-renewal-${contract.id}`,
              type: 'renewal',
              severity: 'warning',
              message: `Contrato "${contract.name}" precisa de renovação em ${contract.renewalNoticeDays} dias`,
              date: renewalDate,
              dismissed: false,
            });
          }
        }

        // Alerta de expiração
        const daysToExpiration = Math.ceil(
          (contract.endDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24)
        );

        if (daysToExpiration <= 30 && daysToExpiration > 0) {
          alerts.push({
            id: `alert-expiration-${contract.id}`,
            type: 'expiration',
            severity: daysToExpiration <= 7 ? 'critical' : 'warning',
            message: `Contrato "${contract.name}" expira em ${daysToExpiration} dias`,
            date: contract.endDate,
            dismissed: false,
          });
        }

        // Alerta de pagamento
        if (contract.nextPaymentDate) {
          const daysToPayment = Math.ceil(
            (contract.nextPaymentDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24)
          );

          if (daysToPayment <= 7 && daysToPayment >= 0) {
            alerts.push({
              id: `alert-payment-${contract.id}`,
              type: 'payment',
              severity: daysToPayment <= 3 ? 'critical' : 'warning',
              message: `Pagamento de "${contract.name}" vence em ${daysToPayment} dias`,
              date: contract.nextPaymentDate,
              dismissed: false,
            });
          }
        }

        // Alertas de milestones
        contract.milestones
          .filter((m) => m.status === 'pending')
          .forEach((milestone) => {
            const daysToMilestone = Math.ceil(
              (milestone.date.getTime() - now.getTime()) / (1000 * 60 * 60 * 24)
            );

            if (daysToMilestone <= 7 && daysToMilestone >= 0) {
              alerts.push({
                id: `alert-milestone-${milestone.id}`,
                type: 'milestone',
                severity: 'info',
                message: `Milestone "${milestone.name}" do contrato "${contract.name}" em ${daysToMilestone} dias`,
                date: milestone.date,
                dismissed: false,
              });
            }
          });
      });

      return alerts.sort((a, b) => a.date.getTime() - b.date.getTime());
    } catch (error) {
      console.error('Erro ao verificar alertas:', error);
      return [];
    }
  }

  /**
   * Renovar contrato
   */
  async renewContract(contractId: string, newEndDate: Date): Promise<Contract> {
    try {
      const contract = await this.getContract(contractId);
      if (!contract) throw new Error('Contrato não encontrado');

      const { data, error } = await supabase
        .from('contracts')
        .update({
          end_date: newEndDate,
          status: 'active',
        })
        .eq('id', contractId)
        .select()
        .single();

      if (error) throw error;

      return this.mapContract(data);
    } catch (error) {
      console.error('Erro ao renovar contrato:', error);
      throw error;
    }
  }

  // Métodos auxiliares

  private calculatePaymentAmount(contract: Contract): number {
    switch (contract.paymentFrequency) {
      case 'monthly':
        return contract.value;
      case 'quarterly':
        return contract.value / 3;
      case 'yearly':
        return contract.value / 12;
      case 'one_time':
        return contract.value;
      default:
        return 0;
    }
  }

  private mapContract(data: any): Contract {
    return {
      id: data.id,
      companyId: data.company_id,
      type: data.type,
      name: data.name,
      counterparty: data.counterparty,
      description: data.description,
      value: data.value,
      currency: data.currency,
      startDate: new Date(data.start_date),
      endDate: new Date(data.end_date),
      renewalType: data.renewal_type,
      renewalNoticeDays: data.renewal_notice_days,
      paymentFrequency: data.payment_frequency,
      nextPaymentDate: data.next_payment_date ? new Date(data.next_payment_date) : undefined,
      status: data.status,
      documents: data.documents || [],
      milestones: (data.milestones || []).map((m: any) => ({
        ...m,
        date: new Date(m.date),
      })),
      alerts: (data.alerts || []).map((a: any) => ({
        ...a,
        date: new Date(a.date),
      })),
      createdAt: new Date(data.created_at),
      updatedAt: new Date(data.updated_at),
    };
  }

  private getMockContracts(): Contract[] {
    return [
      {
        id: 'contract-1',
        companyId: 'comp-001',
        type: 'supplier',
        name: 'Fornecimento de Material de Escritório',
        counterparty: 'Papelaria Central Lda',
        description: 'Contrato anual de fornecimento de material de escritório',
        value: 120000,
        currency: 'AOA',
        startDate: new Date('2026-01-01'),
        endDate: new Date('2026-12-31'),
        renewalType: 'automatic',
        renewalNoticeDays: 30,
        paymentFrequency: 'monthly',
        nextPaymentDate: new Date('2026-05-01'),
        status: 'active',
        documents: [],
        milestones: [],
        alerts: [],
        createdAt: new Date('2026-01-01'),
        updatedAt: new Date(),
      },
    ];
  }
}

export const contractManagementService = new ContractManagementService();
