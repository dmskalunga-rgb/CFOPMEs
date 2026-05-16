// =====================================================
// KWANZACONTROL - DOM Safety Utilities
// Utilitários para Operações Seguras no DOM
// =====================================================

/**
 * Remove um nó do DOM de forma segura
 * Previne erro: "Failed to execute 'removeChild' on 'Node'"
 */
export function safeRemoveChild(node: Node | null | undefined): boolean {
  if (!node) {
    console.warn('[DOM Safety] Attempted to remove null/undefined node');
    return false;
  }

  if (!node.parentNode) {
    console.warn('[DOM Safety] Node has no parent, cannot remove');
    return false;
  }

  try {
    node.parentNode.removeChild(node);
    return true;
  } catch (error) {
    console.error('[DOM Safety] Error removing node:', error);
    return false;
  }
}

/**
 * Remove múltiplos nós de forma segura
 */
export function safeRemoveChildren(nodes: NodeListOf<Node> | Node[]): number {
  let removed = 0;
  
  nodes.forEach(node => {
    if (safeRemoveChild(node)) {
      removed++;
    }
  });

  return removed;
}

/**
 * Verifica se um nó está anexado ao DOM
 */
export function isNodeAttached(node: Node | null | undefined): boolean {
  if (!node) return false;
  
  // Verifica se o nó tem um parentNode
  if (!node.parentNode) return false;
  
  // Verifica se o nó está no documento
  return document.contains(node);
}

/**
 * Limpa um container de forma segura
 */
export function safeClearContainer(container: HTMLElement | null | undefined): boolean {
  if (!container) {
    console.warn('[DOM Safety] Attempted to clear null/undefined container');
    return false;
  }

  try {
    // Método mais seguro que innerHTML = ''
    while (container.firstChild) {
      safeRemoveChild(container.firstChild);
    }
    return true;
  } catch (error) {
    console.error('[DOM Safety] Error clearing container:', error);
    return false;
  }
}

/**
 * Substitui um nó de forma segura
 */
export function safeReplaceChild(
  newNode: Node,
  oldNode: Node | null | undefined
): boolean {
  if (!oldNode || !oldNode.parentNode) {
    console.warn('[DOM Safety] Cannot replace node without parent');
    return false;
  }

  try {
    oldNode.parentNode.replaceChild(newNode, oldNode);
    return true;
  } catch (error) {
    console.error('[DOM Safety] Error replacing node:', error);
    return false;
  }
}

/**
 * Valida integridade do DOM antes de operações
 */
export function validateDOMIntegrity(node: Node | null | undefined): {
  valid: boolean;
  issues: string[];
} {
  const issues: string[] = [];

  if (!node) {
    issues.push('Node is null or undefined');
    return { valid: false, issues };
  }

  if (!node.parentNode) {
    issues.push('Node has no parent');
  }

  if (!document.contains(node)) {
    issues.push('Node is not attached to document');
  }

  return {
    valid: issues.length === 0,
    issues,
  };
}

/**
 * Hook React para operações seguras no DOM
 */
export function useSafeDOM() {
  const removeChild = (node: Node | null | undefined) => {
    return safeRemoveChild(node);
  };

  const clearContainer = (container: HTMLElement | null | undefined) => {
    return safeClearContainer(container);
  };

  const replaceChild = (newNode: Node, oldNode: Node | null | undefined) => {
    return safeReplaceChild(newNode, oldNode);
  };

  const validateIntegrity = (node: Node | null | undefined) => {
    return validateDOMIntegrity(node);
  };

  return {
    removeChild,
    clearContainer,
    replaceChild,
    validateIntegrity,
    isNodeAttached,
  };
}

/**
 * Decorator para métodos que manipulam DOM
 */
export function withDOMSafety<T extends (...args: any[]) => any>(
  fn: T,
  errorHandler?: (error: Error) => void
): T {
  return ((...args: any[]) => {
    try {
      return fn(...args);
    } catch (error) {
      console.error('[DOM Safety] Error in DOM operation:', error);
      if (errorHandler) {
        errorHandler(error as Error);
      }
      return null;
    }
  }) as T;
}

/**
 * Monitora mudanças no DOM e detecta inconsistências
 */
export function createDOMMonitor(
  target: Node,
  callback: (mutations: MutationRecord[]) => void
): MutationObserver {
  const observer = new MutationObserver((mutations) => {
    // Filtrar mutações problemáticas
    const problematicMutations = mutations.filter(mutation => {
      // Detectar remoções de nós que ainda têm referências
      if (mutation.type === 'childList' && mutation.removedNodes.length > 0) {
        return Array.from(mutation.removedNodes).some(node => {
          return !isNodeAttached(node) && node.parentNode !== null;
        });
      }
      return false;
    });

    if (problematicMutations.length > 0) {
      console.warn('[DOM Monitor] Detected problematic mutations:', problematicMutations);
    }

    callback(mutations);
  });

  observer.observe(target, {
    childList: true,
    subtree: true,
    attributes: false,
  });

  return observer;
}

/**
 * Cleanup automático para componentes React
 */
export function createDOMCleanup() {
  const refs = new Set<Node>();

  const register = (node: Node) => {
    refs.add(node);
  };

  const cleanup = () => {
    refs.forEach(node => {
      safeRemoveChild(node);
    });
    refs.clear();
  };

  return { register, cleanup };
}
