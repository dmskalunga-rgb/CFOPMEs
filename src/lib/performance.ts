// Performance Utils - Utilitários para otimização de performance

/**
 * Debounce - Atrasa execução de função
 */
export function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number = 300
): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null;

  return function executedFunction(...args: Parameters<T>) {
    const later = () => {
      timeout = null;
      func(...args);
    };

    if (timeout) {
      clearTimeout(timeout);
    }
    timeout = setTimeout(later, wait);
  };
}

/**
 * Throttle - Limita execução de função
 */
export function throttle<T extends (...args: any[]) => any>(
  func: T,
  limit: number = 300
): (...args: Parameters<T>) => void {
  let inThrottle: boolean;

  return function executedFunction(...args: Parameters<T>) {
    if (!inThrottle) {
      func(...args);
      inThrottle = true;
      setTimeout(() => (inThrottle = false), limit);
    }
  };
}

/**
 * Memoize - Cache de resultados de função
 */
export function memoize<T extends (...args: any[]) => any>(
  func: T
): T {
  const cache = new Map<string, ReturnType<T>>();

  return ((...args: Parameters<T>) => {
    const key = JSON.stringify(args);
    
    if (cache.has(key)) {
      return cache.get(key);
    }

    const result = func(...args);
    cache.set(key, result);
    return result;
  }) as T;
}

/**
 * Lazy Load - Carrega módulo sob demanda
 */
export async function lazyLoad<T>(
  importFn: () => Promise<{ default: T }>
): Promise<T> {
  try {
    const module = await importFn();
    return module.default;
  } catch (error) {
    console.error('Lazy load error:', error);
    throw error;
  }
}

/**
 * Batch - Agrupa múltiplas operações
 */
export function batch<T>(
  operations: Array<() => Promise<T>>,
  batchSize: number = 5
): Promise<T[]> {
  const batches: Array<Array<() => Promise<T>>> = [];
  
  for (let i = 0; i < operations.length; i += batchSize) {
    batches.push(operations.slice(i, i + batchSize));
  }

  return batches.reduce(
    async (previousBatch, currentBatch) => {
      const results = await previousBatch;
      const batchResults = await Promise.all(
        currentBatch.map(op => op())
      );
      return [...results, ...batchResults];
    },
    Promise.resolve([] as T[])
  );
}

/**
 * Retry - Tenta executar função com retry
 */
export async function retry<T>(
  fn: () => Promise<T>,
  maxAttempts: number = 3,
  delay: number = 1000
): Promise<T> {
  let lastError: Error;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error as Error;
      
      if (attempt < maxAttempts) {
        await new Promise(resolve => setTimeout(resolve, delay * attempt));
      }
    }
  }

  throw lastError!;
}

/**
 * Measure Performance - Mede tempo de execução
 */
export async function measurePerformance<T>(
  name: string,
  fn: () => Promise<T>
): Promise<T> {
  const start = performance.now();
  
  try {
    const result = await fn();
    const end = performance.now();
    
    if (process.env.NODE_ENV === 'development') {
      console.log(`[Performance] ${name}: ${(end - start).toFixed(2)}ms`);
    }
    
    return result;
  } catch (error) {
    const end = performance.now();
    console.error(`[Performance] ${name} failed after ${(end - start).toFixed(2)}ms`);
    throw error;
  }
}

/**
 * Chunk Array - Divide array em chunks
 */
export function chunkArray<T>(array: T[], size: number): T[][] {
  const chunks: T[][] = [];
  
  for (let i = 0; i < array.length; i += size) {
    chunks.push(array.slice(i, i + size));
  }
  
  return chunks;
}

/**
 * Deep Clone - Clona objeto profundamente
 */
export function deepClone<T>(obj: T): T {
  if (obj === null || typeof obj !== 'object') {
    return obj;
  }

  if (obj instanceof Date) {
    return new Date(obj.getTime()) as any;
  }

  if (obj instanceof Array) {
    return obj.map(item => deepClone(item)) as any;
  }

  if (obj instanceof Object) {
    const clonedObj = {} as T;
    for (const key in obj) {
      if (obj.hasOwnProperty(key)) {
        clonedObj[key] = deepClone(obj[key]);
      }
    }
    return clonedObj;
  }

  return obj;
}

/**
 * Compress Data - Comprime dados para localStorage
 */
export function compressData(data: any): string {
  try {
    return btoa(JSON.stringify(data));
  } catch (error) {
    console.error('Compression error:', error);
    return JSON.stringify(data);
  }
}

/**
 * Decompress Data - Descomprime dados do localStorage
 */
export function decompressData<T>(compressed: string): T | null {
  try {
    return JSON.parse(atob(compressed));
  } catch (error) {
    try {
      return JSON.parse(compressed);
    } catch {
      console.error('Decompression error:', error);
      return null;
    }
  }
}

/**
 * Prefetch Links - Prefetch de links para navegação rápida
 */
export function prefetchLinks(selector: string = 'a[href^="/"]'): void {
  if (typeof window === 'undefined') return;

  const links = document.querySelectorAll(selector);
  
  links.forEach(link => {
    const href = link.getAttribute('href');
    if (!href) return;

    const prefetchLink = document.createElement('link');
    prefetchLink.rel = 'prefetch';
    prefetchLink.href = href;
    document.head.appendChild(prefetchLink);
  });
}

/**
 * Optimize Images - Otimiza carregamento de imagens
 */
export function optimizeImages(): void {
  if (typeof window === 'undefined') return;

  const images = document.querySelectorAll('img[data-src]');
  
  const imageObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const img = entry.target as HTMLImageElement;
        const src = img.getAttribute('data-src');
        
        if (src) {
          img.src = src;
          img.removeAttribute('data-src');
          imageObserver.unobserve(img);
        }
      }
    });
  });

  images.forEach(img => imageObserver.observe(img));
}

/**
 * Request Idle Callback - Executa função quando navegador estiver ocioso
 */
export function requestIdleCallback(
  callback: () => void,
  options?: { timeout?: number }
): void {
  if (typeof window === 'undefined') return;

  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(callback, options);
  } else {
    setTimeout(callback, 1);
  }
}

/**
 * Get Performance Metrics - Obtém métricas de performance
 */
export function getPerformanceMetrics(): {
  loadTime: number;
  domContentLoaded: number;
  firstPaint: number;
  firstContentfulPaint: number;
} | null {
  if (typeof window === 'undefined' || !window.performance) {
    return null;
  }

  const navigation = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
  const paint = performance.getEntriesByType('paint');

  return {
    loadTime: navigation?.loadEventEnd - navigation?.fetchStart || 0,
    domContentLoaded: navigation?.domContentLoadedEventEnd - navigation?.fetchStart || 0,
    firstPaint: paint.find(entry => entry.name === 'first-paint')?.startTime || 0,
    firstContentfulPaint: paint.find(entry => entry.name === 'first-contentful-paint')?.startTime || 0,
  };
}
