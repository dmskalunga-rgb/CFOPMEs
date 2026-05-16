// =====================================================
// KWANZACONTROL - Quota Alert Component
// Alerta visual quando quotas estão próximas do limite
// =====================================================

import { AlertCircle, TrendingUp } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { useNavigate } from 'react-router-dom';
import { ROUTE_PATHS } from '@/lib/index';
import type { QuotaCheck } from '@/services/quotaService';

interface QuotaAlertProps {
  quota: QuotaCheck;
  resourceName: string;
  showUpgrade?: boolean;
}

export function QuotaAlert({ quota, resourceName, showUpgrade = true }: QuotaAlertProps) {
  const navigate = useNavigate();

  // Não mostrar se ainda tem muito espaço
  if (quota.percentage < 70) {
    return null;
  }

  const isNearLimit = quota.percentage >= 70 && quota.percentage < 90;
  const isAtLimit = quota.percentage >= 90;

  return (
    <Alert variant={isAtLimit ? 'destructive' : 'default'} className="mb-4">
      <AlertCircle className="h-4 w-4" />
      <AlertTitle>
        {isAtLimit ? 'Limite Atingido' : 'Próximo do Limite'}
      </AlertTitle>
      <AlertDescription className="space-y-3">
        <p>
          Você está usando <strong>{quota.used}</strong> de <strong>{quota.limit === Infinity ? '∞' : quota.limit}</strong> {resourceName}.
        </p>
        
        {quota.limit !== Infinity && (
          <div className="space-y-1">
            <div className="flex justify-between text-sm">
              <span>{quota.percentage.toFixed(0)}% usado</span>
              <span>{quota.remaining} restantes</span>
            </div>
            <Progress value={quota.percentage} className="h-2" />
          </div>
        )}

        {isAtLimit && (
          <p className="text-sm font-medium">
            {quota.message}
          </p>
        )}

        {showUpgrade && (
          <Button
            size="sm"
            onClick={() => navigate(ROUTE_PATHS.BILLING)}
            className="mt-2"
          >
            <TrendingUp className="w-4 h-4 mr-2" />
            Fazer Upgrade
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}

// Componente para mostrar múltiplas quotas
interface QuotaDashboardProps {
  quotas: {
    users?: QuotaCheck;
    invoices?: QuotaCheck;
    employees?: QuotaCheck;
    transactions?: QuotaCheck;
    reports?: QuotaCheck;
    storage?: QuotaCheck;
  };
}

export function QuotaDashboard({ quotas }: QuotaDashboardProps) {
  const hasWarnings = Object.values(quotas).some(q => q && q.percentage >= 70);

  if (!hasWarnings) {
    return null;
  }

  return (
    <div className="space-y-3">
      {quotas.users && quotas.users.percentage >= 70 && (
        <QuotaAlert quota={quotas.users} resourceName="usuários" />
      )}
      {quotas.invoices && quotas.invoices.percentage >= 70 && (
        <QuotaAlert quota={quotas.invoices} resourceName="faturas este mês" />
      )}
      {quotas.employees && quotas.employees.percentage >= 70 && (
        <QuotaAlert quota={quotas.employees} resourceName="funcionários" />
      )}
      {quotas.transactions && quotas.transactions.percentage >= 70 && (
        <QuotaAlert quota={quotas.transactions} resourceName="transações este mês" />
      )}
      {quotas.reports && quotas.reports.percentage >= 70 && (
        <QuotaAlert quota={quotas.reports} resourceName="relatórios este mês" />
      )}
      {quotas.storage && quotas.storage.percentage >= 70 && (
        <QuotaAlert quota={quotas.storage} resourceName="GB de armazenamento" />
      )}
    </div>
  );
}
