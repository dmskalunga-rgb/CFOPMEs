// =====================================================
// KWANZACONTROL - Quota Guard Component
// Componente wrapper para proteger ações com verificação de quota
// =====================================================

import { ReactNode } from 'react';
import { useToast } from '@/hooks/use-toast';
import { canPerformAction } from '@/services/quotaService';

interface QuotaGuardProps {
  action: 'create_user' | 'create_invoice' | 'create_employee' | 'create_transaction' | 'create_report';
  onAllowed: () => void;
  children: ReactNode;
}

export function QuotaGuard({ action, onAllowed, children }: QuotaGuardProps) {
  const { toast } = useToast();

  const handleClick = async () => {
    const check = await canPerformAction(action);
    
    if (!check.allowed) {
      toast({
        title: 'Limite atingido',
        description: check.message,
        variant: 'destructive',
      });
      return;
    }

    onAllowed();
  };

  return (
    <div onClick={handleClick}>
      {children}
    </div>
  );
}

// Hook para usar em formulários
export function useQuotaGuard() {
  const { toast } = useToast();

  const checkQuota = async (
    action: 'create_user' | 'create_invoice' | 'create_employee' | 'create_transaction' | 'create_report'
  ): Promise<boolean> => {
    const check = await canPerformAction(action);
    
    if (!check.allowed) {
      toast({
        title: 'Limite atingido',
        description: check.message,
        variant: 'destructive',
      });
      return false;
    }

    return true;
  };

  return { checkQuota };
}
