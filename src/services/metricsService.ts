// Metrics Service
import { supabase } from '@/integrations/supabase/client';

export interface GeneralMetrics {
  revenue: { total: number; growth: number; trend: string };
  customers: { total: number; new: number; active: number; churn: number };
  sales: { total: number; growth: number; avgTicket: number; conversion: number };
}

export interface KPI {
  id: string;
  kpi_name: string;
  kpi_value: string;
  kpi_change?: string;
  trend: string;
  target_value?: string;
  category?: string;
}

export interface ProductMetric {
  product_name: string;
  sales: number;
  revenue: number;
  growth: number;
}

export interface CustomerMetric {
  customer_name: string;
  purchases: number;
  revenue: number;
  last_purchase: string;
}

class MetricsService {
  async getGeneralMetrics(): Promise<GeneralMetrics> {
    const { data, error } = await supabase.rpc('get_general_metrics_2026_04_09');
    if (error) throw error;
    return data;
  }

  async getKPIs(): Promise<KPI[]> {
    const { data, error } = await supabase
      .from('metrics_kpis_2026_04_09')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(10);
    if (error) throw error;
    return data || [];
  }

  async getTopProducts(limit: number = 10): Promise<ProductMetric[]> {
    const { data, error } = await supabase.rpc('get_top_products_2026_04_09', {
      limit_count: limit
    });
    if (error) throw error;
    return data || [];
  }

  async getTopCustomers(limit: number = 10): Promise<CustomerMetric[]> {
    const { data, error } = await supabase.rpc('get_top_customers_2026_04_09', {
      limit_count: limit
    });
    if (error) throw error;
    return data || [];
  }

  formatCurrency(value: number): string {
    return new Intl.NumberFormat('pt-AO', {
      style: 'currency',
      currency: 'AOA'
    }).format(value);
  }

  formatNumber(value: number): string {
    return new Intl.NumberFormat('pt-AO').format(value);
  }
}

export const metricsService = new MetricsService();
