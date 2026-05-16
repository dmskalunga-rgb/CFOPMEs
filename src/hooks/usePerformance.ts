// Performance Hooks - Otimizações de Performance
import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Hook para debounce de valores
 * Útil para inputs de busca e filtros
 */
export function useDebounce<T>(value: T, delay: number = 500): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(handler);
    };
  }, [value, delay]);

  return debouncedValue;
}

/**
 * Hook para throttle de funções
 * Útil para eventos de scroll e resize
 */
export function useThrottle<T extends (...args: any[]) => any>(
  callback: T,
  delay: number = 500
): T {
  const lastRun = useRef(Date.now());

  return useCallback(
    ((...args) => {
      const now = Date.now();
      if (now - lastRun.current >= delay) {
        callback(...args);
        lastRun.current = now;
      }
    }) as T,
    [callback, delay]
  );
}

/**
 * Hook para lazy loading de imagens
 * Carrega imagens apenas quando visíveis
 */
export function useLazyImage(src: string): {
  imageSrc: string | undefined;
  isLoading: boolean;
} {
  const [imageSrc, setImageSrc] = useState<string | undefined>(undefined);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const img = new Image();
    img.src = src;
    
    img.onload = () => {
      setImageSrc(src);
      setIsLoading(false);
    };

    img.onerror = () => {
      setIsLoading(false);
    };

    return () => {
      img.onload = null;
      img.onerror = null;
    };
  }, [src]);

  return { imageSrc, isLoading };
}

/**
 * Hook para intersection observer
 * Detecta quando elemento está visível
 */
export function useIntersectionObserver(
  ref: React.RefObject<Element>,
  options: IntersectionObserverInit = {}
): boolean {
  const [isIntersecting, setIsIntersecting] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const observer = new IntersectionObserver(([entry]) => {
      setIsIntersecting(entry.isIntersecting);
    }, options);

    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [ref, options]);

  return isIntersecting;
}

/**
 * Hook para medir performance de componentes
 * Útil para debugging
 */
export function usePerformance(componentName: string) {
  const renderCount = useRef(0);
  const startTime = useRef(Date.now());

  useEffect(() => {
    renderCount.current += 1;
    const endTime = Date.now();
    const renderTime = endTime - startTime.current;

    if (process.env.NODE_ENV === 'development') {
      console.log(`[Performance] ${componentName}:`, {
        renders: renderCount.current,
        lastRenderTime: `${renderTime}ms`,
      });
    }

    startTime.current = Date.now();
  });

  return {
    renderCount: renderCount.current,
  };
}

/**
 * Hook para cache local
 * Armazena dados no localStorage com TTL
 */
export function useLocalCache<T>(
  key: string,
  ttl: number = 3600000 // 1 hora padrão
): {
  data: T | null;
  setData: (data: T) => void;
  clearData: () => void;
} {
  const [data, setDataState] = useState<T | null>(() => {
    try {
      const item = localStorage.getItem(key);
      if (!item) return null;

      const parsed = JSON.parse(item);
      const now = Date.now();

      if (now - parsed.timestamp > ttl) {
        localStorage.removeItem(key);
        return null;
      }

      return parsed.data;
    } catch {
      return null;
    }
  });

  const setData = useCallback(
    (newData: T) => {
      try {
        const item = {
          data: newData,
          timestamp: Date.now(),
        };
        localStorage.setItem(key, JSON.stringify(item));
        setDataState(newData);
      } catch (error) {
        console.error('Error saving to localStorage:', error);
      }
    },
    [key]
  );

  const clearData = useCallback(() => {
    try {
      localStorage.removeItem(key);
      setDataState(null);
    } catch (error) {
      console.error('Error clearing localStorage:', error);
    }
  }, [key]);

  return { data, setData, clearData };
}

/**
 * Hook para prefetch de dados
 * Carrega dados antes de serem necessários
 */
export function usePrefetch<T>(
  fetchFn: () => Promise<T>,
  shouldPrefetch: boolean = false
): {
  data: T | null;
  isLoading: boolean;
  error: Error | null;
} {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!shouldPrefetch) return;

    let cancelled = false;

    const prefetch = async () => {
      try {
        setIsLoading(true);
        const result = await fetchFn();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err as Error);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    prefetch();

    return () => {
      cancelled = true;
    };
  }, [fetchFn, shouldPrefetch]);

  return { data, isLoading, error };
}

/**
 * Hook para virtual scrolling
 * Renderiza apenas itens visíveis em listas grandes
 */
export function useVirtualScroll<T>(
  items: T[],
  itemHeight: number,
  containerHeight: number
): {
  visibleItems: T[];
  startIndex: number;
  endIndex: number;
  totalHeight: number;
  offsetY: number;
} {
  const [scrollTop, setScrollTop] = useState(0);

  const startIndex = Math.floor(scrollTop / itemHeight);
  const endIndex = Math.min(
    startIndex + Math.ceil(containerHeight / itemHeight) + 1,
    items.length
  );

  const visibleItems = items.slice(startIndex, endIndex);
  const totalHeight = items.length * itemHeight;
  const offsetY = startIndex * itemHeight;

  return {
    visibleItems,
    startIndex,
    endIndex,
    totalHeight,
    offsetY,
  };
}

/**
 * Hook para batch updates
 * Agrupa múltiplas atualizações em uma só
 */
export function useBatchUpdate<T>(
  initialValue: T,
  delay: number = 100
): [T, (value: T) => void, () => void] {
  const [value, setValue] = useState<T>(initialValue);
  const [pendingValue, setPendingValue] = useState<T | null>(null);
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  const batchSetValue = useCallback(
    (newValue: T) => {
      setPendingValue(newValue);

      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }

      timeoutRef.current = setTimeout(() => {
        setValue(newValue);
        setPendingValue(null);
      }, delay);
    },
    [delay]
  );

  const flush = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    if (pendingValue !== null) {
      setValue(pendingValue);
      setPendingValue(null);
    }
  }, [pendingValue]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return [value, batchSetValue, flush];
}
