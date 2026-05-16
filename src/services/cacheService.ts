// Cache Service - Sistema de cache inteligente para API
interface CacheEntry<T> {
  data: T;
  timestamp: number;
  expiresAt: number;
}

class CacheService {
  private cache: Map<string, CacheEntry<any>> = new Map();
  private defaultTTL: number = 5 * 60 * 1000; // 5 minutos

  /**
   * Obter dados do cache
   */
  get<T>(key: string): T | null {
    const entry = this.cache.get(key);
    
    if (!entry) {
      return null;
    }

    // Verificar se expirou
    if (Date.now() > entry.expiresAt) {
      this.cache.delete(key);
      return null;
    }

    return entry.data;
  }

  /**
   * Salvar dados no cache
   */
  set<T>(key: string, data: T, ttl?: number): void {
    const expiresAt = Date.now() + (ttl || this.defaultTTL);
    
    this.cache.set(key, {
      data,
      timestamp: Date.now(),
      expiresAt,
    });
  }

  /**
   * Remover item do cache
   */
  delete(key: string): void {
    this.cache.delete(key);
  }

  /**
   * Limpar todo o cache
   */
  clear(): void {
    this.cache.clear();
  }

  /**
   * Invalidar cache por padrão
   */
  invalidatePattern(pattern: string): void {
    const regex = new RegExp(pattern);
    
    for (const key of this.cache.keys()) {
      if (regex.test(key)) {
        this.cache.delete(key);
      }
    }
  }

  /**
   * Obter ou buscar dados (cache-first)
   */
  async getOrFetch<T>(
    key: string,
    fetchFn: () => Promise<T>,
    ttl?: number
  ): Promise<T> {
    // Tentar obter do cache
    const cached = this.get<T>(key);
    if (cached !== null) {
      return cached;
    }

    // Buscar dados
    const data = await fetchFn();
    
    // Salvar no cache
    this.set(key, data, ttl);
    
    return data;
  }

  /**
   * Prefetch de dados
   */
  async prefetch<T>(
    key: string,
    fetchFn: () => Promise<T>,
    ttl?: number
  ): Promise<void> {
    // Não fazer nada se já existe no cache
    if (this.get(key) !== null) {
      return;
    }

    try {
      const data = await fetchFn();
      this.set(key, data, ttl);
    } catch (error) {
      console.error('Prefetch error:', error);
    }
  }

  /**
   * Obter estatísticas do cache
   */
  getStats(): {
    size: number;
    keys: string[];
    oldestEntry: number | null;
    newestEntry: number | null;
  } {
    const entries = Array.from(this.cache.entries());
    
    return {
      size: this.cache.size,
      keys: Array.from(this.cache.keys()),
      oldestEntry: entries.length > 0
        ? Math.min(...entries.map(([, entry]) => entry.timestamp))
        : null,
      newestEntry: entries.length > 0
        ? Math.max(...entries.map(([, entry]) => entry.timestamp))
        : null,
    };
  }

  /**
   * Limpar entradas expiradas
   */
  cleanup(): void {
    const now = Date.now();
    
    for (const [key, entry] of this.cache.entries()) {
      if (now > entry.expiresAt) {
        this.cache.delete(key);
      }
    }
  }
}

// Instância singleton
export const cacheService = new CacheService();

// Limpar cache expirado a cada 5 minutos
if (typeof window !== 'undefined') {
  setInterval(() => {
    cacheService.cleanup();
  }, 5 * 60 * 1000);
}

// Exemplo de uso:
// const data = await cacheService.getOrFetch(
//   'users-list',
//   () => fetchUsers(),
//   10 * 60 * 1000 // 10 minutos
// );
