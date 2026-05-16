// Marketplace Service — 100% Supabase, sem dados simulados
// Tabelas: plugins, installed_plugins
import { supabase } from '@/integrations/supabase/client';

export interface Plugin {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  version: string;
  author: string;
  category: string;
  icon_url: string | null;
  homepage_url: string | null;
  repository_url: string | null;
  pricing_type: 'free' | 'paid' | 'subscription' | 'freemium';
  price: number | null;
  currency: string;
  permissions: string[];
  status: string;
  install_count: number;
  rating: number;
  review_count: number;
  created_at: string;
  installed?: boolean;
  installation_id?: string;
}

export interface InstalledPlugin {
  id: string;
  plugin_id: string;
  config: Record<string, unknown>;
  status: string;
  installed_at: string;
  plugin?: Plugin;
}

export interface MarketplaceStats {
  total_plugins: number;
  free_plugins: number;
  installed_count: number;
  avg_rating: number;
  categories: number;
}

// Helper: obter user_id e company_id
async function getUserContext(): Promise<{ userId: string; companyId: string } | null> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;
  const { data: profile } = await supabase
    .from('users')
    .select('tenant_id')
    .eq('id', user.id)
    .maybeSingle();
  return {
    userId: user.id,
    companyId: profile?.tenant_id ?? user.id,
  };
}

class MarketplaceServiceDirect {
  // ── PLUGINS ───────────────────────────────────────────────────────────────

  async listPlugins(params?: {
    category?: string;
    search?: string;
    pricing_type?: string;
  }): Promise<Plugin[]> {
    const ctx = await getUserContext();

    let query = supabase
      .from('plugins')
      .select('*')
      .eq('status', 'approved')
      .order('install_count', { ascending: false });

    if (params?.category) {
      query = query.eq('category', params.category);
    }
    if (params?.search) {
      query = query.or(`name.ilike.%${params.search}%,description.ilike.%${params.search}%`);
    }
    if (params?.pricing_type) {
      query = query.eq('pricing_type', params.pricing_type);
    }

    const { data: pluginsData, error } = await query;
    if (error) throw new Error(error.message);

    const plugins = (pluginsData ?? []) as Plugin[];

    // Se autenticado, verificar quais estão instalados
    if (ctx) {
      const { data: installed } = await supabase
        .from('installed_plugins')
        .select('plugin_id, id')
        .eq('user_id', ctx.userId);

      const installedMap = new Map((installed ?? []).map(i => [i.plugin_id, i.id]));

      return plugins.map(p => ({
        ...p,
        permissions: Array.isArray(p.permissions) ? p.permissions : [],
        installed: installedMap.has(p.id),
        installation_id: installedMap.get(p.id),
      }));
    }

    return plugins.map(p => ({
      ...p,
      permissions: Array.isArray(p.permissions) ? p.permissions : [],
    }));
  }

  async getCategories(): Promise<string[]> {
    const { data, error } = await supabase
      .from('plugins')
      .select('category')
      .eq('status', 'approved');
    if (error) throw new Error(error.message);
    const cats = new Set((data ?? []).map(r => r.category));
    return Array.from(cats).sort();
  }

  async getStats(): Promise<MarketplaceStats> {
    const ctx = await getUserContext();

    const { data: pluginsData } = await supabase
      .from('plugins')
      .select('pricing_type, rating, category')
      .eq('status', 'approved');

    const plugins = pluginsData ?? [];
    const freeCount = plugins.filter(p => p.pricing_type === 'free').length;
    const avgRating = plugins.length > 0
      ? plugins.reduce((s, p) => s + (p.rating ?? 0), 0) / plugins.length
      : 0;
    const categories = new Set(plugins.map(p => p.category)).size;

    let installedCount = 0;
    if (ctx) {
      const { count } = await supabase
        .from('installed_plugins')
        .select('id', { count: 'exact', head: true })
        .eq('user_id', ctx.userId);
      installedCount = count ?? 0;
    }

    return {
      total_plugins: plugins.length,
      free_plugins: freeCount,
      installed_count: installedCount,
      avg_rating: avgRating,
      categories,
    };
  }

  // ── INSTALAÇÕES ──────────────────────────────────────────────────────────

  async installPlugin(pluginId: string): Promise<InstalledPlugin> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');

    // Verificar se já está instalado
    const { data: existing } = await supabase
      .from('installed_plugins')
      .select('id')
      .eq('user_id', ctx.userId)
      .eq('plugin_id', pluginId)
      .maybeSingle();

    if (existing) throw new Error('Plugin já instalado');

    const { data, error } = await supabase
      .from('installed_plugins')
      .insert({
        user_id: ctx.userId,
        company_id: ctx.companyId,
        plugin_id: pluginId,
        config: {},
        status: 'active',
      })
      .select()
      .single();

    if (error) throw new Error(error.message);

    // Incrementar contador de instalação
    // Incrementar install_count
    void supabase.from('plugins')
      .select('install_count')
      .eq('id', pluginId)
      .single()
      .then(({ data: p }) => {
        if (p) {
          supabase.from('plugins').update({ install_count: (p.install_count ?? 0) + 1 }).eq('id', pluginId);
        }
      });

    return {
      id: data.id,
      plugin_id: data.plugin_id,
      config: data.config ?? {},
      status: data.status,
      installed_at: data.installed_at,
    };
  }

  async uninstallPlugin(pluginId: string): Promise<void> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');

    const { error } = await supabase
      .from('installed_plugins')
      .delete()
      .eq('plugin_id', pluginId)
      .eq('user_id', ctx.userId);

    if (error) throw new Error(error.message);
  }

  async togglePlugin(installationId: string, isActive: boolean): Promise<void> {
    const ctx = await getUserContext();
    if (!ctx) throw new Error('Utilizador não autenticado');

    const { error } = await supabase
      .from('installed_plugins')
      .update({ status: isActive ? 'active' : 'inactive', updated_at: new Date().toISOString() })
      .eq('id', installationId)
      .eq('user_id', ctx.userId);

    if (error) throw new Error(error.message);
  }

  async listInstalled(): Promise<InstalledPlugin[]> {
    const ctx = await getUserContext();
    if (!ctx) return [];

    const { data, error } = await supabase
      .from('installed_plugins')
      .select(`
        id, plugin_id, config, status, installed_at,
        plugins!inner(*)
      `)
      .eq('user_id', ctx.userId)
      .order('installed_at', { ascending: false });

    if (error) throw new Error(error.message);

    return (data ?? []).map(row => ({
      id: row.id,
      plugin_id: row.plugin_id,
      config: (row.config as Record<string, unknown>) ?? {},
      status: row.status,
      installed_at: row.installed_at,
      plugin: row.plugins as unknown as Plugin,
    }));
  }
}

export const marketplaceServiceDirect = new MarketplaceServiceDirect();
