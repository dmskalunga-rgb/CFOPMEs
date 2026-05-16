// =====================================================
// KWANZACONTROL - Smart Company Service
// Serviço para Camada Core de Empresa Inteligente
// Data: 2026-04-07
// =====================================================

import { supabase } from '@/integrations/supabase/client';

// =====================================================
// TIPOS
// =====================================================

export interface SmartCompany {
  id: string;
  name: string;
  nif: string;
  country: string;
  currency: string;
  fiscal_regime: string;
  plan_id?: string;
  active_modules: string[];
  ai_profile_id?: string;
  context_vector_id?: string;
  metadata?: any;
  created_at: string;
  updated_at: string;
}

export interface CompanyAIProfile {
  id: string;
  company_id: string;
  behavior_score: number;
  risk_score: number;
  growth_score: number;
  compliance_score: number;
  financial_pattern_model?: any;
  employee_pattern_model?: any;
  transaction_pattern_model?: any;
  company_classification?: string;
  risk_level?: string;
  last_analysis_at?: string;
  next_analysis_at?: string;
}

export interface CompanyPrediction {
  id: string;
  company_id: string;
  prediction_type: 'REVENUE' | 'CASHFLOW' | 'RISK' | 'GROWTH' | 'CHURN';
  prediction_date: string;
  prediction_period: string;
  predicted_value: number;
  confidence_score: number;
  prediction_details?: any;
  factors?: string[];
  model_version?: string;
  created_at: string;
}

export interface CompanyAIInsight {
  id: string;
  company_id: string;
  insight_type: string;
  title: string;
  content: string;
  summary?: string;
  priority: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
  action_required: boolean;
  action_type?: string;
  metadata?: any;
  status: 'ACTIVE' | 'DISMISSED' | 'ACTIONED';
  created_at: string;
}

export interface CompanyModule {
  id: string;
  company_id: string;
  module_id: string;
  module_name: string;
  enabled: boolean;
  usage_score: number;
  total_uses: number;
  last_used_at?: string;
  recommended: boolean;
  recommendation_reason?: string;
  recommendation_score?: number;
}

// =====================================================
// 1. GESTÃO DE EMPRESAS
// =====================================================

export const companyService = {
  // Criar empresa com IA automática
  async createSmartCompany(data: {
    name: string;
    nif: string;
    fiscal_regime: string;
    country?: string;
    currency?: string;
  }) {
    const { data: result, error } = await supabase.rpc('create_smart_company_with_ai', {
      p_name: data.name,
      p_nif: data.nif,
      p_fiscal_regime: data.fiscal_regime,
      p_country: data.country || 'AO',
      p_currency: data.currency || 'AOA',
    });

    if (error) throw error;
    return result;
  },

  // Listar empresas
  async listCompanies() {
    const { data, error } = await supabase
      .from('smart_companies_2026_04_07')
      .select('*')
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data as SmartCompany[];
  },

  // Buscar empresa por ID
  async getCompany(companyId: string) {
    const { data, error } = await supabase
      .from('smart_companies_2026_04_07')
      .select('*')
      .eq('id', companyId)
      .single();

    if (error) throw error;
    return data as SmartCompany;
  },

  // Atualizar empresa
  async updateCompany(companyId: string, updates: Partial<SmartCompany>) {
    const { data, error } = await supabase
      .from('smart_companies_2026_04_07')
      .update(updates)
      .eq('id', companyId)
      .select()
      .single();

    if (error) throw error;
    return data as SmartCompany;
  },
};

// =====================================================
// 2. PERFIL DE IA
// =====================================================

export const aiProfileService = {
  // Analisar perfil de IA
  async analyzeProfile(companyId: string) {
    const { data, error } = await supabase.functions.invoke('company_ai_analyzer_fixed_2026_04_08', {
      body: { companyId },
    });

    if (error) throw error;
    return data;
  },

  // Buscar perfil de IA
  async getProfile(companyId: string) {
    const { data, error } = await supabase
      .from('company_ai_profiles_2026_04_07')
      .select('*')
      .eq('company_id', companyId)
      .single();

    if (error) throw error;
    return data as CompanyAIProfile;
  },
};

// =====================================================
// 3. PREVISÕES
// =====================================================

export const predictionService = {
  // Gerar previsão
  async generatePrediction(
    companyId: string,
    predictionType: 'REVENUE' | 'CASHFLOW' | 'RISK' | 'GROWTH' | 'CHURN',
    period: 'NEXT_MONTH' | 'NEXT_QUARTER' | 'NEXT_YEAR' = 'NEXT_MONTH'
  ) {
    const { data, error } = await supabase.functions.invoke('company_prediction_fixed_2026_04_08', {
      body: { companyId, predictionType, period },
    });

    if (error) throw error;
    return data;
  },

  // Listar previsões
  async listPredictions(companyId: string, predictionType?: string) {
    let query = supabase
      .from('company_predictions_2026_04_07')
      .select('*')
      .eq('company_id', companyId)
      .order('created_at', { ascending: false });

    if (predictionType) {
      query = query.eq('prediction_type', predictionType);
    }

    const { data, error } = await query;

    if (error) throw error;
    return data as CompanyPrediction[];
  },

  // Buscar última previsão por tipo
  async getLatestPrediction(companyId: string, predictionType: string) {
    const { data, error } = await supabase
      .from('company_predictions_2026_04_07')
      .select('*')
      .eq('company_id', companyId)
      .eq('prediction_type', predictionType)
      .order('created_at', { ascending: false })
      .limit(1)
      .single();

    if (error && error.code !== 'PGRST116') throw error;
    return data as CompanyPrediction | null;
  },
};

// =====================================================
// 4. INSIGHTS DE IA
// =====================================================

export const insightsService = {
  // Gerar insight
  async generateInsight(
    companyId: string,
    insightType: 'FINANCIAL_REPORT' | 'ANOMALY_EXPLANATION' | 'OPTIMIZATION_SUGGESTION' | 'RISK_ALERT' | 'OPPORTUNITY'
  ) {
    const { data, error } = await supabase.functions.invoke('company_ai_insights_fixed_2026_04_08', {
      body: { companyId, insightType },
    });

    if (error) throw error;
    return data;
  },

  // Listar insights
  async listInsights(companyId: string, status: 'ACTIVE' | 'DISMISSED' | 'ACTIONED' = 'ACTIVE') {
    const { data, error } = await supabase
      .from('company_ai_insights_2026_04_07')
      .select('*')
      .eq('company_id', companyId)
      .eq('status', status)
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data as CompanyAIInsight[];
  },

  // Marcar insight como lido/dispensado
  async dismissInsight(insightId: string) {
    const { data, error } = await supabase
      .from('company_ai_insights_2026_04_07')
      .update({
        status: 'DISMISSED',
        dismissed_at: new Date().toISOString(),
      })
      .eq('id', insightId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Marcar insight como acionado
  async actionInsight(insightId: string) {
    const { data, error } = await supabase
      .from('company_ai_insights_2026_04_07')
      .update({
        status: 'ACTIONED',
        actioned_at: new Date().toISOString(),
      })
      .eq('id', insightId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },
};

// =====================================================
// 5. MÓDULOS
// =====================================================

export const modulesService = {
  // Listar módulos da empresa
  async listModules(companyId: string) {
    const { data, error } = await supabase
      .from('company_modules_2026_04_07')
      .select('*')
      .eq('company_id', companyId)
      .order('usage_score', { ascending: false });

    if (error) throw error;
    return data as CompanyModule[];
  },

  // Ativar módulo
  async enableModule(companyId: string, moduleId: string, moduleName: string) {
    const { data, error } = await supabase
      .from('company_modules_2026_04_07')
      .upsert({
        company_id: companyId,
        module_id: moduleId,
        module_name: moduleName,
        enabled: true,
        enabled_at: new Date().toISOString(),
      }, {
        onConflict: 'company_id,module_id',
      })
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Desativar módulo
  async disableModule(companyId: string, moduleId: string) {
    const { data, error } = await supabase
      .from('company_modules_2026_04_07')
      .update({ enabled: false })
      .eq('company_id', companyId)
      .eq('module_id', moduleId)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Registrar uso de módulo
  async recordModuleUsage(companyId: string, moduleId: string) {
    const { data, error } = await supabase
      .from('company_modules_2026_04_07')
      .update({
        total_uses: supabase.rpc('increment', { x: 1 }),
        last_used_at: new Date().toISOString(),
      })
      .eq('company_id', companyId)
      .eq('module_id', moduleId)
      .select()
      .single();

    if (error) console.error('Error recording module usage:', error);
    return data;
  },
};

// =====================================================
// 6. DASHBOARD CONSOLIDADO
// =====================================================

export const dashboardService = {
  // Buscar dados completos do dashboard
  async getDashboardData(companyId: string) {
    try {
      const [company, aiProfile, predictions, insights, modules] = await Promise.all([
        companyService.getCompany(companyId),
        aiProfileService.getProfile(companyId).catch((): null => null),
        predictionService.listPredictions(companyId).catch((): CompanyPrediction[] => []),
        insightsService.listInsights(companyId).catch((): CompanyAIInsight[] => []),
        modulesService.listModules(companyId).catch((): CompanyModule[] => []),
      ]);

      return {
        company,
        aiProfile,
        predictions,
        insights,
        modules,
      };
    } catch (error) {
      console.error('Error fetching dashboard data:', error);
      throw error;
    }
  },

  // Executar análise completa
  async runFullAnalysis(companyId: string) {
    try {
      // 1. Analisar perfil de IA
      const profileAnalysis = await aiProfileService.analyzeProfile(companyId);

      // 2. Gerar previsões
      const predictions = await Promise.all([
        predictionService.generatePrediction(companyId, 'REVENUE'),
        predictionService.generatePrediction(companyId, 'RISK'),
        predictionService.generatePrediction(companyId, 'GROWTH'),
      ]);

      // 3. Gerar insights
      const insights = await Promise.all([
        insightsService.generateInsight(companyId, 'FINANCIAL_REPORT'),
        insightsService.generateInsight(companyId, 'OPTIMIZATION_SUGGESTION'),
      ]);

      return {
        profileAnalysis,
        predictions,
        insights,
      };
    } catch (error) {
      console.error('Error running full analysis:', error);
      throw error;
    }
  },
};

// Exportar tudo
export default {
  company: companyService,
  aiProfile: aiProfileService,
  predictions: predictionService,
  insights: insightsService,
  modules: modulesService,
  dashboard: dashboardService,
};
