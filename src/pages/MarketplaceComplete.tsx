// MarketplaceComplete — Marketplace de Integrações e Extensões (100% Supabase)
import { useState, useEffect, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { motion } from 'framer-motion';
import {
  Search, Star, Download, Package, CheckCircle2, RefreshCw,
  Zap, Store, ShieldCheck, Globe, CreditCard, Mail, Bell,
  BarChart3, Settings, Loader2, XCircle, ToggleLeft, ToggleRight,
  AlertCircle, Plug, Filter, TrendingUp
} from 'lucide-react';
import { toast } from 'sonner';
import { marketplaceServiceDirect, Plugin, MarketplaceStats } from '@/services/marketplaceServiceDirect';

// ── Ícone por categoria ───────────────────────────────────────────────────────
const categoryIcon: Record<string, React.ReactNode> = {
  'Pagamentos': <CreditCard className="h-4 w-4" />,
  'Comunicação': <Mail className="h-4 w-4" />,
  'Fiscal': <ShieldCheck className="h-4 w-4" />,
  'Analytics': <BarChart3 className="h-4 w-4" />,
  'CRM': <Globe className="h-4 w-4" />,
  'Contabilidade': <Settings className="h-4 w-4" />,
  'Automação': <Zap className="h-4 w-4" />,
};

const categoryColor: Record<string, string> = {
  'Pagamentos': 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300',
  'Comunicação': 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300',
  'Fiscal': 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
  'Analytics': 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  'CRM': 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
  'Contabilidade': 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300',
  'Automação': 'bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300',
};

// ── Formatar preço ────────────────────────────────────────────────────────────
function formatPrice(plugin: Plugin): string {
  if (plugin.pricing_type === 'free') return 'Grátis';
  if (plugin.pricing_type === 'freemium') return `Freemium · ${fmtAOA(plugin.price)}`;
  if (plugin.pricing_type === 'subscription') return `${fmtAOA(plugin.price)}/mês`;
  return fmtAOA(plugin.price);
}

function fmtAOA(value: number | null): string {
  if (!value) return 'Grátis';
  return new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
}

// ── Card de plugin ────────────────────────────────────────────────────────────
function PluginCard({
  plugin,
  onInstall,
  onUninstall,
  onDetail,
  installing,
}: {
  plugin: Plugin;
  onInstall: (p: Plugin) => void;
  onUninstall: (p: Plugin) => void;
  onDetail: (p: Plugin) => void;
  installing: string | null;
}) {
  const isInstalling = installing === plugin.id;
  const catColor = categoryColor[plugin.category] ?? 'bg-muted text-muted-foreground';

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
    >
      <Card className={`h-full flex flex-col transition-all hover:shadow-md ${plugin.installed ? 'ring-1 ring-primary/30' : ''}`}>
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center text-lg flex-shrink-0">
                {plugin.icon_url ? (
                  <img src={plugin.icon_url} alt={plugin.name} className="w-8 h-8 object-contain rounded" />
                ) : (
                  <Package className="h-5 w-5 text-muted-foreground" />
                )}
              </div>
              <div className="min-w-0">
                <CardTitle className="text-base leading-tight">{plugin.name}</CardTitle>
                <p className="text-xs text-muted-foreground mt-0.5">por {plugin.author}</p>
              </div>
            </div>
            <div className="flex flex-col items-end gap-1 flex-shrink-0">
              {plugin.installed && (
                <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 text-xs">
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                  Instalado
                </Badge>
              )}
              <Badge variant="outline" className={`text-xs ${catColor}`}>
                {categoryIcon[plugin.category]}
                <span className="ml-1">{plugin.category}</span>
              </Badge>
            </div>
          </div>
        </CardHeader>

        <CardContent className="flex-1 flex flex-col gap-3">
          <p className="text-sm text-muted-foreground line-clamp-2 flex-1">
            {plugin.description}
          </p>

          {/* Stats */}
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Star className="h-3.5 w-3.5 fill-yellow-400 text-yellow-400" />
              <span className="font-medium text-foreground">{Number(plugin.rating).toFixed(1)}</span>
              <span>({plugin.review_count})</span>
            </span>
            <span className="flex items-center gap-1">
              <Download className="h-3.5 w-3.5" />
              {(plugin.install_count ?? 0).toLocaleString()}
            </span>
            <span className="flex items-center gap-1">
              <span>v{plugin.version}</span>
            </span>
          </div>

          {/* Preço + Acções */}
          <div className="flex items-center justify-between gap-2 pt-1 border-t">
            <span className={`text-sm font-semibold ${plugin.pricing_type === 'free' ? 'text-emerald-600' : 'text-foreground'}`}>
              {formatPrice(plugin)}
            </span>
            <div className="flex gap-1.5">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={() => onDetail(plugin)}
              >
                Detalhes
              </Button>
              {plugin.installed ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-7 text-xs"
                  onClick={() => onUninstall(plugin)}
                  disabled={isInstalling}
                >
                  {isInstalling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Remover'}
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  onClick={() => onInstall(plugin)}
                  disabled={isInstalling}
                >
                  {isInstalling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Instalar'}
                </Button>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ── Componente principal ──────────────────────────────────────────────────────
export default function MarketplaceComplete() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [stats, setStats] = useState<MarketplaceStats | null>(null);
  const [categories, setCategories] = useState<string[]>([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedFilter, setSelectedFilter] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [detailPlugin, setDetailPlugin] = useState<Plugin | null>(null);
  const [activeTab, setActiveTab] = useState('all');
  const [error, setError] = useState<string | null>(null);

  // ── Carregar dados ──────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    try {
      setError(null);
      setRefreshing(true);
      const [pluginsData, statsData, catsData] = await Promise.all([
        marketplaceServiceDirect.listPlugins(),
        marketplaceServiceDirect.getStats(),
        marketplaceServiceDirect.getCategories(),
      ]);
      setPlugins(pluginsData);
      setStats(statsData);
      setCategories(catsData);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao carregar marketplace';
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // ── Filtrar plugins ─────────────────────────────────────────────────────
  const filtered = plugins.filter(p => {
    const matchSearch =
      !searchTerm ||
      p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (p.description ?? '').toLowerCase().includes(searchTerm.toLowerCase()) ||
      p.author.toLowerCase().includes(searchTerm.toLowerCase());
    const matchCategory = !selectedCategory || p.category === selectedCategory;
    const matchFilter =
      !selectedFilter ||
      (selectedFilter === 'free' && p.pricing_type === 'free') ||
      (selectedFilter === 'paid' && p.pricing_type !== 'free') ||
      (selectedFilter === 'top' && Number(p.rating) >= 4.7);
    const matchTab =
      activeTab === 'all' ||
      (activeTab === 'installed' && p.installed) ||
      (activeTab === 'free' && p.pricing_type === 'free');
    return matchSearch && matchCategory && matchFilter && matchTab;
  });

  // ── Instalar plugin ─────────────────────────────────────────────────────
  const handleInstall = async (plugin: Plugin) => {
    setInstalling(plugin.id);
    try {
      await marketplaceServiceDirect.installPlugin(plugin.id);
      toast.success(`✅ ${plugin.name} instalado com sucesso!`);
      await loadData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao instalar';
      toast.error(msg);
    } finally {
      setInstalling(null);
    }
  };

  // ── Desinstalar plugin ──────────────────────────────────────────────────
  const handleUninstall = async (plugin: Plugin) => {
    if (!confirm(`Remover "${plugin.name}"? Esta acção não pode ser desfeita.`)) return;
    setInstalling(plugin.id);
    try {
      await marketplaceServiceDirect.uninstallPlugin(plugin.id);
      toast.success(`${plugin.name} removido.`);
      await loadData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao remover';
      toast.error(msg);
    } finally {
      setInstalling(null);
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <Layout>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="space-y-6"
      >
        {/* Header */}
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
              <Store className="h-8 w-8 text-primary" />
              Marketplace
            </h1>
            <p className="text-muted-foreground mt-1">
              Integrações e extensões para KwanzaControl
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={loadData} disabled={refreshing}>
            <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
            Actualizar
          </Button>
        </div>

        {/* Erro */}
        {error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* KPI Cards */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                  <Package className="h-4 w-4" /> Total
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stats.total_plugins}</div>
                <p className="text-xs text-muted-foreground">{stats.categories} categorias</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" /> Grátis
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-emerald-600">{stats.free_plugins}</div>
                <p className="text-xs text-muted-foreground">disponíveis</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                  <Plug className="h-4 w-4 text-primary" /> Instaladas
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-primary">{stats.installed_count}</div>
                <p className="text-xs text-muted-foreground">activas</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                  <Star className="h-4 w-4 text-yellow-500" /> Rating
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stats.avg_rating.toFixed(1)}</div>
                <p className="text-xs text-muted-foreground">média geral</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                  <TrendingUp className="h-4 w-4 text-blue-500" /> Downloads
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {plugins.reduce((s, p) => s + (p.install_count ?? 0), 0).toLocaleString()}
                </div>
                <p className="text-xs text-muted-foreground">totais</p>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Filtros e Pesquisa */}
        <Card>
          <CardContent className="pt-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Pesquisar integrações..."
                  value={searchTerm}
                  onChange={e => setSearchTerm(e.target.value)}
                  className="pl-9"
                />
              </div>
              <div className="flex gap-2 flex-wrap">
                {/* Filtro por preço */}
                {['free', 'paid', 'top'].map(f => (
                  <Button
                    key={f}
                    variant={selectedFilter === f ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setSelectedFilter(selectedFilter === f ? null : f)}
                    className="h-8 text-xs"
                  >
                    <Filter className="h-3 w-3 mr-1" />
                    {f === 'free' ? 'Grátis' : f === 'paid' ? 'Pago' : 'Top ⭐'}
                  </Button>
                ))}
              </div>
            </div>

            {/* Categorias */}
            {categories.length > 0 && (
              <div className="flex gap-2 flex-wrap mt-3">
                <Button
                  variant={!selectedCategory ? 'default' : 'outline'}
                  size="sm"
                  className="h-7 text-xs"
                  onClick={() => setSelectedCategory(null)}
                >
                  Todas
                </Button>
                {categories.map(cat => (
                  <Button
                    key={cat}
                    variant={selectedCategory === cat ? 'default' : 'outline'}
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => setSelectedCategory(selectedCategory === cat ? null : cat)}
                  >
                    {categoryIcon[cat]}
                    <span className="ml-1">{cat}</span>
                  </Button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Tabs */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
          <TabsList>
            <TabsTrigger value="all">
              Todas
              <Badge variant="secondary" className="ml-1.5 text-xs">{plugins.length}</Badge>
            </TabsTrigger>
            <TabsTrigger value="installed">
              Instaladas
              <Badge variant="secondary" className="ml-1.5 text-xs">
                {plugins.filter(p => p.installed).length}
              </Badge>
            </TabsTrigger>
            <TabsTrigger value="free">
              Grátis
            </TabsTrigger>
          </TabsList>

          <TabsContent value={activeTab} className="space-y-4">
            {loading ? (
              <div className="flex items-center justify-center py-20">
                <Loader2 className="h-8 w-8 animate-spin text-primary" />
                <span className="ml-3 text-muted-foreground">A carregar marketplace...</span>
              </div>
            ) : filtered.length === 0 ? (
              <div className="text-center py-16 space-y-3">
                <Store className="h-12 w-12 text-muted-foreground mx-auto" />
                <p className="text-muted-foreground">
                  {activeTab === 'installed' ? 'Nenhuma integração instalada ainda.' : 'Nenhuma integração encontrada.'}
                </p>
                {(searchTerm || selectedCategory || selectedFilter) && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => { setSearchTerm(''); setSelectedCategory(null); setSelectedFilter(null); }}
                  >
                    Limpar filtros
                  </Button>
                )}
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {filtered.map(plugin => (
                  <PluginCard
                    key={plugin.id}
                    plugin={plugin}
                    onInstall={handleInstall}
                    onUninstall={handleUninstall}
                    onDetail={setDetailPlugin}
                    installing={installing}
                  />
                ))}
              </div>
            )}
          </TabsContent>
        </Tabs>
      </motion.div>

      {/* Modal de Detalhe */}
      {detailPlugin && (
        <Dialog open={!!detailPlugin} onOpenChange={() => setDetailPlugin(null)}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center">
                  <Package className="h-5 w-5 text-muted-foreground" />
                </div>
                <div>
                  <p>{detailPlugin.name}</p>
                  <p className="text-xs font-normal text-muted-foreground">por {detailPlugin.author} · v{detailPlugin.version}</p>
                </div>
              </DialogTitle>
              <DialogDescription className="text-left pt-2">
                {detailPlugin.description}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4">
              {/* Métricas */}
              <div className="grid grid-cols-3 gap-3">
                <div className="text-center p-3 bg-muted/40 rounded-lg">
                  <div className="flex items-center justify-center gap-1">
                    <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" />
                    <span className="font-bold">{Number(detailPlugin.rating).toFixed(1)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">{detailPlugin.review_count} avaliações</p>
                </div>
                <div className="text-center p-3 bg-muted/40 rounded-lg">
                  <div className="flex items-center justify-center gap-1">
                    <Download className="h-4 w-4 text-primary" />
                    <span className="font-bold">{(detailPlugin.install_count ?? 0).toLocaleString()}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">instalações</p>
                </div>
                <div className="text-center p-3 bg-muted/40 rounded-lg">
                  <div className="font-bold text-emerald-600">{formatPrice(detailPlugin)}</div>
                  <p className="text-xs text-muted-foreground mt-1">preço</p>
                </div>
              </div>

              {/* Permissões */}
              {detailPlugin.permissions.length > 0 && (
                <div>
                  <p className="text-sm font-semibold mb-2 flex items-center gap-1">
                    <ShieldCheck className="h-4 w-4 text-muted-foreground" />
                    Permissões necessárias
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {detailPlugin.permissions.map(p => (
                      <Badge key={p} variant="outline" className="text-xs font-mono">{p}</Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* Links */}
              {(detailPlugin.homepage_url || detailPlugin.repository_url) && (
                <div className="flex gap-2">
                  {detailPlugin.homepage_url && (
                    <a href={detailPlugin.homepage_url} target="_blank" rel="noopener noreferrer">
                      <Button variant="outline" size="sm" className="text-xs">
                        <Globe className="h-3.5 w-3.5 mr-1" /> Website
                      </Button>
                    </a>
                  )}
                </div>
              )}

              {/* Acção */}
              <div className="flex gap-2 pt-2 border-t">
                <Button variant="outline" className="flex-1" onClick={() => setDetailPlugin(null)}>
                  Fechar
                </Button>
                {detailPlugin.installed ? (
                  <Button
                    variant="destructive"
                    className="flex-1"
                    onClick={async () => {
                      setDetailPlugin(null);
                      await handleUninstall(detailPlugin);
                    }}
                  >
                    <XCircle className="h-4 w-4 mr-2" />
                    Remover
                  </Button>
                ) : (
                  <Button
                    className="flex-1"
                    onClick={async () => {
                      setDetailPlugin(null);
                      await handleInstall(detailPlugin);
                    }}
                    disabled={installing === detailPlugin.id}
                  >
                    {installing === detailPlugin.id ? (
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    ) : (
                      <Download className="h-4 w-4 mr-2" />
                    )}
                    Instalar
                  </Button>
                )}
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </Layout>
  );
}
