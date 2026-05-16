import { supabase } from '@/integrations/supabase/client';

export interface Category {
  id: string;
  name: string;
  slug: string;
  description?: string;
  icon?: string;
  display_order: number;
  is_active: boolean;
  created_at: string;
}

export interface Plugin {
  id: string;
  name: string;
  slug: string;
  description: string;
  long_description?: string;
  category_id: string;
  version: string;
  author?: string;
  author_email?: string;
  icon?: string;
  price: number;
  is_free: boolean;
  is_featured: boolean;
  is_active: boolean;
  download_count: number;
  install_count: number;
  rating_average: number;
  rating_count: number;
  tags?: string[];
  screenshots?: string[];
  documentation_url?: string;
  support_url?: string;
  homepage_url?: string;
  repository_url?: string;
  changelog?: string;
  requirements?: any;
  config_schema?: any;
  created_at: string;
  updated_at: string;
}

export interface Installation {
  id: string;
  user_id: string;
  plugin_id: string;
  installed_version: string;
  is_active: boolean;
  config?: any;
  installed_at: string;
  updated_at: string;
  plugin?: Plugin;
}

export interface Review {
  id: string;
  user_id: string;
  plugin_id: string;
  rating: number;
  title?: string;
  comment?: string;
  is_verified_purchase: boolean;
  created_at: string;
  updated_at: string;
}

export interface MarketplaceStats {
  total_plugins: number;
  active_plugins: number;
  featured_plugins: number;
  total_downloads: number;
  total_installs: number;
  avg_rating: number;
  user_installed: number;
}

class MarketplaceService {
  private async callEdgeFunction(action: string, params: any = {}) {
    const { data: { session } } = await supabase.auth.getSession();
    
    if (!session) {
      throw new Error('Not authenticated');
    }

    const { data, error } = await supabase.functions.invoke('marketplace_manager_2026_04_09', {
      body: { action, ...params },
      headers: {
        Authorization: `Bearer ${session.access_token}`,
      },
    });

    if (error) throw error;
    if (!data.success) throw new Error(data.error || 'Operation failed');

    return data;
  }

  async listCategories(): Promise<Category[]> {
    const data = await this.callEdgeFunction('list_categories');
    return data.categories;
  }

  async listPlugins(params?: {
    category_id?: string;
    search?: string;
    is_featured?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<Plugin[]> {
    const data = await this.callEdgeFunction('list_plugins', params);
    return data.plugins;
  }

  async getPlugin(plugin_id: string): Promise<Plugin> {
    const data = await this.callEdgeFunction('get_plugin', { plugin_id });
    return data.plugin;
  }

  async listInstalled(): Promise<Installation[]> {
    const data = await this.callEdgeFunction('list_installed');
    return data.installations;
  }

  async installPlugin(plugin_id: string): Promise<Installation> {
    const data = await this.callEdgeFunction('install_plugin', { plugin_id });
    return data.installation;
  }

  async uninstallPlugin(plugin_id: string): Promise<void> {
    await this.callEdgeFunction('uninstall_plugin', { plugin_id });
  }

  async togglePlugin(plugin_id: string, is_active: boolean): Promise<void> {
    await this.callEdgeFunction('toggle_plugin', { plugin_id, is_active });
  }

  async addReview(params: {
    plugin_id: string;
    rating: number;
    title?: string;
    comment?: string;
  }): Promise<Review> {
    const data = await this.callEdgeFunction('add_review', params);
    return data.review;
  }

  async listReviews(plugin_id: string, limit = 10, offset = 0): Promise<Review[]> {
    const data = await this.callEdgeFunction('list_reviews', { plugin_id, limit, offset });
    return data.reviews;
  }

  async getStats(): Promise<MarketplaceStats> {
    const data = await this.callEdgeFunction('get_stats');
    return data.stats;
  }

  // Helper method to check if plugin is installed
  async isPluginInstalled(plugin_id: string): Promise<boolean> {
    try {
      const installations = await this.listInstalled();
      return installations.some(i => i.plugin_id === plugin_id && i.is_active);
    } catch {
      return false;
    }
  }
}

export const marketplaceService = new MarketplaceService();
