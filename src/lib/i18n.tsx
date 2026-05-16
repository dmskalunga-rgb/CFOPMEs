// =====================================================
// KWANZACONTROL - Internationalization (i18n)
// Sistema de internacionalização PT-AO e EN
// Data: 2026-04-04
// =====================================================

import { createContext, useContext, useState, ReactNode } from 'react';

type Language = 'pt-AO' | 'en';

interface Translations {
  [key: string]: {
    'pt-AO': string;
    'en': string;
  };
}

const translations: Translations = {
  // Common
  'common.loading': { 'pt-AO': 'Carregando...', 'en': 'Loading...' },
  'common.save': { 'pt-AO': 'Salvar', 'en': 'Save' },
  'common.cancel': { 'pt-AO': 'Cancelar', 'en': 'Cancel' },
  'common.delete': { 'pt-AO': 'Deletar', 'en': 'Delete' },
  'common.edit': { 'pt-AO': 'Editar', 'en': 'Edit' },
  'common.create': { 'pt-AO': 'Criar', 'en': 'Create' },
  'common.search': { 'pt-AO': 'Buscar', 'en': 'Search' },
  'common.filter': { 'pt-AO': 'Filtrar', 'en': 'Filter' },
  'common.export': { 'pt-AO': 'Exportar', 'en': 'Export' },
  'common.import': { 'pt-AO': 'Importar', 'en': 'Import' },
  'common.yes': { 'pt-AO': 'Sim', 'en': 'Yes' },
  'common.no': { 'pt-AO': 'Não', 'en': 'No' },
  'common.confirm': { 'pt-AO': 'Confirmar', 'en': 'Confirm' },
  'common.back': { 'pt-AO': 'Voltar', 'en': 'Back' },
  'common.next': { 'pt-AO': 'Próximo', 'en': 'Next' },
  'common.previous': { 'pt-AO': 'Anterior', 'en': 'Previous' },
  'common.close': { 'pt-AO': 'Fechar', 'en': 'Close' },

  // Dashboard
  'dashboard.title': { 'pt-AO': 'Dashboard', 'en': 'Dashboard' },
  'dashboard.welcome': { 'pt-AO': 'Bem-vindo ao KWANZACONTROL', 'en': 'Welcome to KWANZACONTROL' },
  'dashboard.revenue': { 'pt-AO': 'Receita', 'en': 'Revenue' },
  'dashboard.expenses': { 'pt-AO': 'Despesas', 'en': 'Expenses' },
  'dashboard.profit': { 'pt-AO': 'Lucro', 'en': 'Profit' },
  'dashboard.invoices': { 'pt-AO': 'Faturas', 'en': 'Invoices' },

  // Invoices
  'invoices.title': { 'pt-AO': 'Faturas', 'en': 'Invoices' },
  'invoices.create': { 'pt-AO': 'Criar Fatura', 'en': 'Create Invoice' },
  'invoices.number': { 'pt-AO': 'Número', 'en': 'Number' },
  'invoices.customer': { 'pt-AO': 'Cliente', 'en': 'Customer' },
  'invoices.date': { 'pt-AO': 'Data', 'en': 'Date' },
  'invoices.amount': { 'pt-AO': 'Valor', 'en': 'Amount' },
  'invoices.status': { 'pt-AO': 'Estado', 'en': 'Status' },
  'invoices.paid': { 'pt-AO': 'Pago', 'en': 'Paid' },
  'invoices.pending': { 'pt-AO': 'Pendente', 'en': 'Pending' },
  'invoices.overdue': { 'pt-AO': 'Vencido', 'en': 'Overdue' },

  // Approvals
  'approvals.title': { 'pt-AO': 'Centro de Aprovações', 'en': 'Approval Center' },
  'approvals.pending': { 'pt-AO': 'Pendentes', 'en': 'Pending' },
  'approvals.approved': { 'pt-AO': 'Aprovados', 'en': 'Approved' },
  'approvals.rejected': { 'pt-AO': 'Rejeitados', 'en': 'Rejected' },
  'approvals.approve': { 'pt-AO': 'Aprovar', 'en': 'Approve' },
  'approvals.reject': { 'pt-AO': 'Rejeitar', 'en': 'Reject' },
  'approvals.comments': { 'pt-AO': 'Comentários', 'en': 'Comments' },

  // Security
  'security.2fa': { 'pt-AO': 'Autenticação de Dois Fatores', 'en': 'Two-Factor Authentication' },
  'security.enable': { 'pt-AO': 'Ativar', 'en': 'Enable' },
  'security.disable': { 'pt-AO': 'Desativar', 'en': 'Disable' },
  'security.code': { 'pt-AO': 'Código', 'en': 'Code' },
  'security.verify': { 'pt-AO': 'Verificar', 'en': 'Verify' },

  // Audit
  'audit.title': { 'pt-AO': 'Auditoria', 'en': 'Audit' },
  'audit.logs': { 'pt-AO': 'Logs', 'en': 'Logs' },
  'audit.action': { 'pt-AO': 'Ação', 'en': 'Action' },
  'audit.user': { 'pt-AO': 'Utilizador', 'en': 'User' },
  'audit.date': { 'pt-AO': 'Data', 'en': 'Date' },
  'audit.severity': { 'pt-AO': 'Severidade', 'en': 'Severity' },
};

interface I18nContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string) => string;
  formatCurrency: (amount: number) => string;
  formatDate: (date: Date | string) => string;
}

const I18nContext = createContext<I18nContextType | undefined>(undefined);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>('pt-AO');

  const t = (key: string): string => {
    return translations[key]?.[language] || key;
  };

  const formatCurrency = (amount: number): string => {
    if (language === 'pt-AO') {
      return new Intl.NumberFormat('pt-AO', {
        style: 'currency',
        currency: 'AOA',
      }).format(amount);
    }
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'AOA',
    }).format(amount);
  };

  const formatDate = (date: Date | string): string => {
    const d = typeof date === 'string' ? new Date(date) : date;
    if (language === 'pt-AO') {
      return new Intl.DateTimeFormat('pt-AO', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      }).format(d);
    }
    return new Intl.DateTimeFormat('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    }).format(d);
  };

  return (
    <I18nContext.Provider value={{ language, setLanguage, t, formatCurrency, formatDate }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error('useI18n must be used within I18nProvider');
  }
  return context;
}

export default I18nProvider;
