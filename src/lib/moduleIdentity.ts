// =====================================================
// KWANZACONTROL - Module Identity System
// Sistema de Identidade Imutável para Módulos
// =====================================================

/**
 * CRITICAL: Este arquivo define identificadores IMUTÁVEIS para módulos.
 * NUNCA altere os IDs - apenas os labels podem ser modificados.
 */

export interface ModuleIdentity {
  id: string; // IMUTÁVEL - usado como key no React
  name: string; // MUTÁVEL - label visual
  path: string; // IMUTÁVEL - rota
  category: string; // Categoria do módulo
}

/**
 * Módulos protegidos contra alteração de nome
 */
export const PROTECTED_MODULE_IDS = [
  'module_management',
  'iam_dashboard',
  'pam_dashboard',
  'rbac_dashboard',
  'billing_dashboard',
] as const;

/**
 * Mapeamento de módulos com IDs imutáveis
 */
export const MODULE_REGISTRY: Record<string, ModuleIdentity> = {
  // Core Modules
  dashboard: {
    id: 'dashboard',
    name: 'Dashboard',
    path: '/dashboard',
    category: 'core',
  },
  invoicing: {
    id: 'invoicing',
    name: 'Faturação',
    path: '/invoicing',
    category: 'core',
  },
  payroll: {
    id: 'payroll',
    name: 'Payroll',
    path: '/payroll',
    category: 'core',
  },
  hr_management: {
    id: 'hr_management',
    name: 'RH Inteligente',
    path: '/hr-management',
    category: 'core',
  },
  finance: {
    id: 'finance',
    name: 'Financeiro',
    path: '/finance',
    category: 'core',
  },
  reports: {
    id: 'reports',
    name: 'Relatórios',
    path: '/reports',
    category: 'core',
  },

  // IAM & Security
  iam_dashboard: {
    id: 'iam_dashboard',
    name: 'IAM Dashboard',
    path: '/iam-dashboard',
    category: 'security',
  },
  pam_dashboard: {
    id: 'pam_dashboard',
    name: 'PAM Dashboard',
    path: '/pam-dashboard',
    category: 'security',
  },
  rbac_dashboard: {
    id: 'rbac_dashboard',
    name: 'RBAC + ABAC',
    path: '/rbac-dashboard',
    category: 'security',
  },
  billing_dashboard: {
    id: 'billing_dashboard',
    name: 'Billing Dashboard',
    path: '/billing-dashboard',
    category: 'billing',
  },

  // Module Management (PROTECTED)
  module_management: {
    id: 'module_management',
    name: 'Gestao de Modulos',
    path: '/module-management',
    category: 'admin',
  },

  // AI Modules
  ai_dashboard: {
    id: 'ai_dashboard',
    name: 'Dashboard IA',
    path: '/ai-dashboard',
    category: 'ai',
  },
  ai_ueba: {
    id: 'ai_ueba',
    name: 'UEBA (Anomalias)',
    path: '/ai-ueba',
    category: 'ai',
  },
  ai_reports: {
    id: 'ai_reports',
    name: 'Relatórios IA',
    path: '/ai-reports',
    category: 'ai',
  },
};

/**
 * Obter módulo por ID (seguro)
 */
export function getModuleById(id: string): ModuleIdentity | null {
  return MODULE_REGISTRY[id] || null;
}

/**
 * Obter módulo por path (seguro)
 */
export function getModuleByPath(path: string): ModuleIdentity | null {
  return Object.values(MODULE_REGISTRY).find(m => m.path === path) || null;
}

/**
 * Verificar se módulo é protegido
 */
export function isProtectedModule(id: string): boolean {
  return PROTECTED_MODULE_IDS.includes(id as any);
}

/**
 * Atualizar nome do módulo (com proteção)
 */
export function updateModuleName(id: string, newName: string): boolean {
  if (isProtectedModule(id)) {
    console.warn(`Module ${id} is protected and cannot be renamed`);
    return false;
  }

  const module = MODULE_REGISTRY[id];
  if (module) {
    module.name = newName;
    return true;
  }

  return false;
}

/**
 * Validar integridade do módulo
 */
export function validateModuleIntegrity(id: string): boolean {
  const module = MODULE_REGISTRY[id];
  if (!module) return false;

  // Verificar se ID não foi alterado
  if (module.id !== id) {
    console.error(`Module ID mismatch: expected ${id}, got ${module.id}`);
    return false;
  }

  // Verificar se path existe
  if (!module.path) {
    console.error(`Module ${id} has no path`);
    return false;
  }

  return true;
}

/**
 * Hook React para usar módulo com segurança
 */
export function useModule(id: string) {
  const module = getModuleById(id);
  
  if (!module) {
    console.warn(`Module ${id} not found in registry`);
  }

  return module;
}

/**
 * Utilitário para criar key segura para React
 */
export function getModuleKey(module: ModuleIdentity | string): string {
  if (typeof module === 'string') {
    return module; // Já é um ID
  }
  return module.id; // Usar ID, nunca name
}
