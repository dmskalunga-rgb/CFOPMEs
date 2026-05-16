// =====================================================
// KWANZACONTROL - useQuota Hook
// Hook React para verificar quotas e limites
// =====================================================

import { useState, useEffect } from 'react';
import {
  checkAllQuotas,
  canPerformAction,
  type QuotaCheck,
} from '@/services/quotaService';

export function useQuota() {
  const [quotas, setQuotas] = useState<{
    users: QuotaCheck;
    invoices: QuotaCheck;
    employees: QuotaCheck;
    transactions: QuotaCheck;
    reports: QuotaCheck;
    storage: QuotaCheck;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadQuotas = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await checkAllQuotas();
      setQuotas(data);
    } catch (err: any) {
      console.error('Error loading quotas:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadQuotas();
  }, []);

  const checkAction = async (
    action: 'create_user' | 'create_invoice' | 'create_employee' | 'create_transaction' | 'create_report'
  ): Promise<{ allowed: boolean; message?: string }> => {
    return await canPerformAction(action);
  };

  return {
    quotas,
    loading,
    error,
    reload: loadQuotas,
    checkAction,
  };
}
