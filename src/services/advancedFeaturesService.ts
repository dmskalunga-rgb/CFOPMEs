// =====================================================
// KWANZACONTROL - Advanced Features Service
// Serviço consolidado para funcionalidades avançadas
// Data: 2026-04-05
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// 1. MÓDULO FINANCEIRO AVANÇADO
// =====================================================

export const cashflowService = {
  // Previsão de Fluxo de Caixa
  async predictCashflow(tenantId: string, daysAhead: number = 30) {
    const { data, error } = await supabase.functions.invoke('cashflow_prediction_fixed_2026_04_07', {
      body: { tenantId, daysAhead, includeSeasonality: true },
    });
    if (error) throw error;
    return data;
  },

  async getPredictions(tenantId: string, startDate?: string, endDate?: string) {
    let query = supabase
      .from('cashflow_predictions')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('prediction_date', { ascending: true });

    if (startDate) query = query.gte('prediction_date', startDate);
    if (endDate) query = query.lte('prediction_date', endDate);

    const { data, error } = await query;
    if (error) throw error;
    return data;
  },
};

export const bankReconciliationService = {
  // Reconciliação Bancária
  async reconcile(tenantId: string, bankAccountId: string, statementDate: string, statementBalance: number, transactions: any[]) {
    const { data, error } = await supabase.functions.invoke('bank_reconciliation_ai_2026_04_05', {
      body: { tenantId, bankAccountId, statementDate, statementBalance, transactions },
    });
    if (error) throw error;
    return data;
  },

  async getReconciliations(tenantId: string) {
    const { data, error } = await supabase
      .from('bank_reconciliations')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('statement_date', { ascending: false });
    if (error) throw error;
    return data;
  },

  async getBankTransactions(reconciliationId: string) {
    const { data, error } = await supabase
      .from('bank_transactions')
      .select('*')
      .eq('reconciliation_id', reconciliationId)
      .order('transaction_date', { ascending: false });
    if (error) throw error;
    return data;
  },
};

export const profitabilityService = {
  async analyze(tenantId: string, entityType: string, entityId: string) {
    const { data, error } = await supabase
      .from('profitability_analysis')
      .select('*')
      .eq('tenant_id', tenantId)
      .eq('entity_type', entityType)
      .eq('entity_id', entityId)
      .order('analysis_date', { ascending: false })
      .limit(1)
      .single();
    if (error) throw error;
    return data;
  },

  async getAll(tenantId: string, entityType?: string) {
    let query = supabase
      .from('profitability_analysis')
      .select('*')
      .eq('tenant_id', tenantId);

    if (entityType) query = query.eq('entity_type', entityType);

    const { data, error } = await query.order('analysis_date', { ascending: false });
    if (error) throw error;
    return data;
  },
};

export const customerCreditService = {
  async analyzeCredit(tenantId: string, customerId: string) {
    const { data, error } = await supabase
      .from('customer_credit_analysis')
      .select('*')
      .eq('tenant_id', tenantId)
      .eq('customer_id', customerId)
      .order('analysis_date', { ascending: false })
      .limit(1)
      .single();
    if (error) throw error;
    return data;
  },

  async getAll(tenantId: string, riskLevel?: string) {
    let query = supabase
      .from('customer_credit_analysis')
      .select('*')
      .eq('tenant_id', tenantId);

    if (riskLevel) query = query.eq('risk_level', riskLevel);

    const { data, error } = await query.order('analysis_date', { ascending: false });
    if (error) throw error;
    return data;
  },
};

// =====================================================
// 2. MÓDULO DE FATURAÇÃO AVANÇADO
// =====================================================

export const collectionService = {
  async getWorkflows(tenantId: string, status?: string) {
    let query = supabase
      .from('collection_workflows')
      .select('*')
      .eq('tenant_id', tenantId);

    if (status) query = query.eq('status', status);

    const { data, error } = await query.order('overdue_days', { ascending: false });
    if (error) throw error;
    return data;
  },

  async updateWorkflow(id: string, updates: any) {
    const { data, error } = await supabase
      .from('collection_workflows')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },
};

export const receivablesService = {
  async getAnalysis(tenantId: string, startDate?: string, endDate?: string) {
    let query = supabase
      .from('receivables_analysis')
      .select('*')
      .eq('tenant_id', tenantId);

    if (startDate) query = query.gte('analysis_date', startDate);
    if (endDate) query = query.lte('analysis_date', endDate);

    const { data, error } = await query.order('analysis_date', { ascending: false });
    if (error) throw error;
    return data;
  },

  async getLatest(tenantId: string) {
    const { data, error } = await supabase
      .from('receivables_analysis')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('analysis_date', { ascending: false })
      .limit(1)
      .single();
    if (error) throw error;
    return data;
  },
};

export const customerPortalService = {
  async createAccess(tenantId: string, customerId: string, customerEmail: string) {
    const accessToken = crypto.randomUUID();
    const { data, error } = await supabase
      .from('customer_portal_access')
      .insert({
        tenant_id: tenantId,
        customer_id: customerId,
        customer_email: customerEmail,
        access_token: accessToken,
        is_active: true,
      })
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async getAccess(tenantId: string, customerId: string) {
    const { data, error } = await supabase
      .from('customer_portal_access')
      .select('*')
      .eq('tenant_id', tenantId)
      .eq('customer_id', customerId)
      .single();
    if (error) throw error;
    return data;
  },
};

// =====================================================
// 3. MÓDULO DE RH AVANÇADO
// =====================================================

export const turnoverService = {
  async predictTurnover(tenantId: string, employeeId?: string) {
    const { data, error } = await supabase.functions.invoke('turnover_predictor_mock_2026_04_07', {
      body: { tenantId, employeeId },
    });
    if (error) throw error;
    return data;
  },

  async getPredictions(tenantId: string, riskLevel?: string) {
    let query = supabase
      .from('turnover_predictions')
      .select('*')
      .eq('tenant_id', tenantId);

    if (riskLevel) query = query.eq('risk_level', riskLevel);

    const { data, error } = await query.order('turnover_risk_score', { ascending: false });
    if (error) throw error;
    return data;
  },

  async updatePrediction(id: string, updates: any) {
    const { data, error } = await supabase
      .from('turnover_predictions')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },
};

export const performanceService = {
  async createReview(review: any) {
    const { data, error } = await supabase
      .from('performance_reviews')
      .insert(review)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async getReviews(tenantId: string, employeeId?: string) {
    let query = supabase
      .from('performance_reviews')
      .select('*')
      .eq('tenant_id', tenantId);

    if (employeeId) query = query.eq('employee_id', employeeId);

    const { data, error } = await query.order('review_period_end', { ascending: false });
    if (error) throw error;
    return data;
  },

  async updateReview(id: string, updates: any) {
    const { data, error } = await supabase
      .from('performance_reviews')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },
};

// =====================================================
// 4. MÓDULO DE BUSINESS INTELLIGENCE
// =====================================================

export const dashboardService = {
  async create(dashboard: any) {
    const { data, error } = await supabase
      .from('custom_dashboards')
      .insert(dashboard)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async getAll(tenantId: string, userId?: string) {
    let query = supabase
      .from('custom_dashboards')
      .select('*')
      .eq('tenant_id', tenantId);

    if (userId) query = query.eq('user_id', userId);

    const { data, error } = await query.order('created_at', { ascending: false });
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: any) {
    const { data, error } = await supabase
      .from('custom_dashboards')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async delete(id: string) {
    const { error } = await supabase
      .from('custom_dashboards')
      .delete()
      .eq('id', id);
    if (error) throw error;
  },
};

export const reportService = {
  async schedule(report: any) {
    const { data, error } = await supabase
      .from('scheduled_reports')
      .insert(report)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async getAll(tenantId: string, isActive?: boolean) {
    let query = supabase
      .from('scheduled_reports')
      .select('*')
      .eq('tenant_id', tenantId);

    if (isActive !== undefined) query = query.eq('is_active', isActive);

    const { data, error } = await query.order('created_at', { ascending: false });
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: any) {
    const { data, error } = await supabase
      .from('scheduled_reports')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },
};

// =====================================================
// 5. MÓDULO DE PLANEJAMENTO FINANCEIRO
// =====================================================

export const budgetService = {
  async create(budget: any) {
    const { data, error } = await supabase
      .from('smart_budgets')
      .insert(budget)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async getAll(tenantId: string, fiscalYear?: number) {
    let query = supabase
      .from('smart_budgets')
      .select('*')
      .eq('tenant_id', tenantId);

    if (fiscalYear) query = query.eq('fiscal_year', fiscalYear);

    const { data, error } = await query.order('created_at', { ascending: false });
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: any) {
    const { data, error } = await supabase
      .from('smart_budgets')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },
};

export const scenarioService = {
  async create(scenario: any) {
    const { data, error } = await supabase
      .from('scenario_simulations')
      .insert(scenario)
      .select()
      .single();
    if (error) throw error;
    return data;
  },

  async getAll(tenantId: string) {
    const { data, error } = await supabase
      .from('scenario_simulations')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false });
    if (error) throw error;
    return data;
  },

  async update(id: string, updates: any) {
    const { data, error } = await supabase
      .from('scenario_simulations')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },
};

// =====================================================
// 6. MÓDULO DE GESTÃO DE ESTOQUE
// =====================================================

export const demandService = {
  async getForecasts(tenantId: string, productId?: string) {
    let query = supabase
      .from('demand_forecasts')
      .select('*')
      .eq('tenant_id', tenantId);

    if (productId) query = query.eq('product_id', productId);

    const { data, error } = await query.order('forecast_date', { ascending: true });
    if (error) throw error;
    return data;
  },
};

export const inventoryService = {
  async getOptimization(tenantId: string, productId?: string) {
    let query = supabase
      .from('inventory_optimization')
      .select('*')
      .eq('tenant_id', tenantId);

    if (productId) query = query.eq('product_id', productId);

    const { data, error } = await query.order('analysis_date', { ascending: false });
    if (error) throw error;
    return data;
  },
};

// =====================================================
// 7. MÓDULO DE CRM
// =====================================================

export const leadService = {
  async getScores(tenantId: string, scoreCategory?: string) {
    let query = supabase
      .from('lead_scores')
      .select('*')
      .eq('tenant_id', tenantId);

    if (scoreCategory) query = query.eq('score_category', scoreCategory);

    const { data, error } = await query.order('score', { ascending: false });
    if (error) throw error;
    return data;
  },

  async updateScore(id: string, updates: any) {
    const { data, error } = await supabase
      .from('lead_scores')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },
};

export const churnService = {
  async getPredictions(tenantId: string, riskLevel?: string) {
    let query = supabase
      .from('churn_predictions')
      .select('*')
      .eq('tenant_id', tenantId);

    if (riskLevel) query = query.eq('risk_level', riskLevel);

    const { data, error } = await query.order('churn_risk_score', { ascending: false });
    if (error) throw error;
    return data;
  },

  async updatePrediction(id: string, updates: any) {
    const { data, error } = await supabase
      .from('churn_predictions')
      .update(updates)
      .eq('id', id)
      .select()
      .single();
    if (error) throw error;
    return data;
  },
};

// Export consolidado
export default {
  cashflow: cashflowService,
  bankReconciliation: bankReconciliationService,
  profitability: profitabilityService,
  customerCredit: customerCreditService,
  collection: collectionService,
  receivables: receivablesService,
  customerPortal: customerPortalService,
  turnover: turnoverService,
  performance: performanceService,
  dashboard: dashboardService,
  report: reportService,
  budget: budgetService,
  scenario: scenarioService,
  demand: demandService,
  inventory: inventoryService,
  lead: leadService,
  churn: churnService,
};
