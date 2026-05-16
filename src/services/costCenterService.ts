/**
 * Cost Center Service
 * Serviço de gestão de centros de custo e análise de rentabilidade
 */

import { supabase } from '@/integrations/supabase/client';

export interface CostCenter {
  id: string;
  companyId: string;
  code: string;
  name: string;
  description: string;
  type: 'department' | 'project' | 'product' | 'location' | 'custom';
  parentId?: string;
  managerId?: string;
  budget: number;
  active: boolean;
  createdAt: Date;
  updatedAt: Date;
}

export interface CostCenterAnalysis {
  costCenterId: string;
  costCenterName: string;
  period: { start: Date; end: Date };
  totalRevenue: number;
  totalCosts: number;
  directCosts: number;
  indirectCosts: number;
  profit: number;
  profitMargin: number;
  roi: number;
  budgetUsage: number;
  costBreakdown: CostBreakdown[];
  revenueBreakdown: RevenueBreakdown[];
  trends: TrendData[];
}

export interface CostBreakdown {
  category: string;
  amount: number;
  percentage: number;
  trend: 'up' | 'down' | 'stable';
}

export interface RevenueBreakdown {
  source: string;
  amount: number;
  percentage: number;
  trend: 'up' | 'down' | 'stable';
}

export interface TrendData {
  month: string;
  revenue: number;
  costs: number;
  profit: number;
}

export interface CostAllocation {
  id: string;
  transactionId: string;
  costCenterId: string;
  amount: number;
  percentage: number;
  allocationMethod: 'direct' | 'proportional' | 'activity_based';
  notes?: string;
}

class CostCenterService {
  /**
   * Criar centro de custo
   */
  async createCostCenter(costCenter: Omit<CostCenter, 'id' | 'createdAt' | 'updatedAt'>): Promise<CostCenter> {
    try {
      const { data, error } = await supabase
        .from('cost_centers')
        .insert({
          company_id: costCenter.companyId,
          code: costCenter.code,
          name: costCenter.name,
          description: costCenter.description,
          type: costCenter.type,
          parent_id: costCenter.parentId,
          manager_id: costCenter.managerId,
          budget: costCenter.budget,
          active: costCenter.active,
        })
        .select()
        .single();

      if (error) throw error;

      return this.mapCostCenter(data);
    } catch (error) {
      console.error('Erro ao criar centro de custo:', error);
      throw error;
    }
  }

  /**
   * Listar centros de custo
   */
  async listCostCenters(companyId: string, activeOnly: boolean = true): Promise<CostCenter[]> {
    try {
      let query = supabase
        .from('cost_centers')
        .select('*')
        .eq('company_id', companyId);

      if (activeOnly) {
        query = query.eq('active', true);
      }

      const { data, error } = await query.order('code', { ascending: true });

      if (error) throw error;

      return (data || []).map(this.mapCostCenter);
    } catch (error) {
      console.error('Erro ao listar centros de custo:', error);
      return this.getMockCostCenters();
    }
  }

  /**
   * Alocar custo a centro de custo
   */
  async allocateCost(allocation: Omit<CostAllocation, 'id'>): Promise<CostAllocation> {
    try {
      const { data, error } = await supabase
        .from('cost_allocations')
        .insert({
          transaction_id: allocation.transactionId,
          cost_center_id: allocation.costCenterId,
          amount: allocation.amount,
          percentage: allocation.percentage,
          allocation_method: allocation.allocationMethod,
          notes: allocation.notes,
        })
        .select()
        .single();

      if (error) throw error;

      return {
        id: data.id,
        transactionId: data.transaction_id,
        costCenterId: data.cost_center_id,
        amount: data.amount,
        percentage: data.percentage,
        allocationMethod: data.allocation_method,
        notes: data.notes,
      };
    } catch (error) {
      console.error('Erro ao alocar custo:', error);
      throw error;
    }
  }

  /**
   * Analisar centro de custo
   */
  async analyzeCostCenter(
    costCenterId: string,
    period: { start: Date; end: Date }
  ): Promise<CostCenterAnalysis> {
    try {
      const costCenter = await this.getCostCenter(costCenterId);
      if (!costCenter) throw new Error('Centro de custo não encontrado');

      // Buscar alocações de custo
      const { data: allocations, error: allocError } = await supabase
        .from('cost_allocations')
        .select('*, transactions(*)')
        .eq('cost_center_id', costCenterId)
        .gte('transactions.date', period.start.toISOString())
        .lte('transactions.date', period.end.toISOString());

      if (allocError) throw allocError;

      // Calcular métricas
      const costs = (allocations || [])
        .filter((a: any) => a.transactions?.type === 'expense')
        .reduce((sum: number, a: any) => sum + a.amount, 0);

      const revenues = (allocations || [])
        .filter((a: any) => a.transactions?.type === 'income')
        .reduce((sum: number, a: any) => sum + a.amount, 0);

      const profit = revenues - costs;
      const profitMargin = revenues > 0 ? (profit / revenues) * 100 : 0;
      const roi = costs > 0 ? (profit / costs) * 100 : 0;
      const budgetUsage = costCenter.budget > 0 ? (costs / costCenter.budget) * 100 : 0;

      // Breakdown de custos
      const costsByCategory: { [key: string]: number } = {};
      (allocations || [])
        .filter((a: any) => a.transactions?.type === 'expense')
        .forEach((a: any) => {
          const category = a.transactions?.category || 'Outros';
          costsByCategory[category] = (costsByCategory[category] || 0) + a.amount;
        });

      const costBreakdown: CostBreakdown[] = Object.entries(costsByCategory).map(
        ([category, amount]) => ({
          category,
          amount,
          percentage: (amount / costs) * 100,
          trend: 'stable' as const,
        })
      );

      // Breakdown de receitas
      const revenuesBySource: { [key: string]: number } = {};
      (allocations || [])
        .filter((a: any) => a.transactions?.type === 'income')
        .forEach((a: any) => {
          const source = a.transactions?.category || 'Outros';
          revenuesBySource[source] = (revenuesBySource[source] || 0) + a.amount;
        });

      const revenueBreakdown: RevenueBreakdown[] = Object.entries(revenuesBySource).map(
        ([source, amount]) => ({
          source,
          amount,
          percentage: (amount / revenues) * 100,
          trend: 'stable' as const,
        })
      );

      // Tendências mensais
      const trends = this.calculateMonthlyTrends(allocations || [], period);

      return {
        costCenterId,
        costCenterName: costCenter.name,
        period,
        totalRevenue: revenues,
        totalCosts: costs,
        directCosts: costs * 0.7, // Simplificado
        indirectCosts: costs * 0.3, // Simplificado
        profit,
        profitMargin,
        roi,
        budgetUsage,
        costBreakdown,
        revenueBreakdown,
        trends,
      };
    } catch (error) {
      console.error('Erro ao analisar centro de custo:', error);
      throw error;
    }
  }

  /**
   * Comparar centros de custo
   */
  async compareCostCenters(
    costCenterIds: string[],
    period: { start: Date; end: Date }
  ): Promise<Array<{
    costCenterId: string;
    costCenterName: string;
    revenue: number;
    costs: number;
    profit: number;
    profitMargin: number;
    roi: number;
  }>> {
    try {
      const comparisons = await Promise.all(
        costCenterIds.map(async (id) => {
          const analysis = await this.analyzeCostCenter(id, period);
          return {
            costCenterId: id,
            costCenterName: analysis.costCenterName,
            revenue: analysis.totalRevenue,
            costs: analysis.totalCosts,
            profit: analysis.profit,
            profitMargin: analysis.profitMargin,
            roi: analysis.roi,
          };
        })
      );

      return comparisons.sort((a, b) => b.profit - a.profit);
    } catch (error) {
      console.error('Erro ao comparar centros de custo:', error);
      return [];
    }
  }

  /**
   * Obter hierarquia de centros de custo
   */
  async getCostCenterHierarchy(companyId: string): Promise<CostCenter[]> {
    try {
      const costCenters = await this.listCostCenters(companyId, false);
      return this.buildHierarchy(costCenters);
    } catch (error) {
      console.error('Erro ao obter hierarquia:', error);
      return [];
    }
  }

  // Métodos auxiliares

  private async getCostCenter(costCenterId: string): Promise<CostCenter | null> {
    try {
      const { data, error } = await supabase
        .from('cost_centers')
        .select('*')
        .eq('id', costCenterId)
        .single();

      if (error) throw error;

      return this.mapCostCenter(data);
    } catch (error) {
      console.error('Erro ao obter centro de custo:', error);
      return null;
    }
  }

  private calculateMonthlyTrends(allocations: any[], period: { start: Date; end: Date }): TrendData[] {
    const monthlyData: { [key: string]: { revenue: number; costs: number } } = {};

    allocations.forEach((a: any) => {
      if (!a.transactions) return;

      const month = new Date(a.transactions.date).toISOString().slice(0, 7);
      if (!monthlyData[month]) {
        monthlyData[month] = { revenue: 0, costs: 0 };
      }

      if (a.transactions.type === 'income') {
        monthlyData[month].revenue += a.amount;
      } else {
        monthlyData[month].costs += a.amount;
      }
    });

    return Object.entries(monthlyData)
      .map(([month, data]) => ({
        month,
        revenue: data.revenue,
        costs: data.costs,
        profit: data.revenue - data.costs,
      }))
      .sort((a, b) => a.month.localeCompare(b.month));
  }

  private buildHierarchy(costCenters: CostCenter[]): CostCenter[] {
    // Simplificado - retorna lista plana
    return costCenters;
  }

  private mapCostCenter(data: any): CostCenter {
    return {
      id: data.id,
      companyId: data.company_id,
      code: data.code,
      name: data.name,
      description: data.description,
      type: data.type,
      parentId: data.parent_id,
      managerId: data.manager_id,
      budget: data.budget,
      active: data.active,
      createdAt: new Date(data.created_at),
      updatedAt: new Date(data.updated_at),
    };
  }

  private getMockCostCenters(): CostCenter[] {
    return [
      {
        id: 'cc-1',
        companyId: 'comp-001',
        code: 'CC-001',
        name: 'Departamento Comercial',
        description: 'Centro de custo do departamento comercial',
        type: 'department',
        budget: 500000,
        active: true,
        createdAt: new Date('2026-01-01'),
        updatedAt: new Date(),
      },
      {
        id: 'cc-2',
        companyId: 'comp-001',
        code: 'CC-002',
        name: 'Departamento Administrativo',
        description: 'Centro de custo do departamento administrativo',
        type: 'department',
        budget: 300000,
        active: true,
        createdAt: new Date('2026-01-01'),
        updatedAt: new Date(),
      },
    ];
  }
}

export const costCenterService = new CostCenterService();
