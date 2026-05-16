import { useState, useEffect } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import {
  Menu,
  X,
  LayoutDashboard,
  FileText,
  Users,
  Wallet,
  BarChart3,
  Settings,
  Bell,
  Moon,
  Sun,
  ChevronRight,
  LogOut,
  User,
  Shield,
  CheckCircle,
  FileSearch,
  Code,
  Store,
  Brain,
  Target,
  Smartphone,
  AlertTriangle,
  Sparkles,
  Activity,
  Upload,
  Repeat,
  Key,
  Bot,
  CreditCard,
  Lock,
  Plug,
  Package,
  Zap,
} from 'lucide-react';
import { ROUTE_PATHS } from '@/lib/index';
import { useAuth } from '@/hooks/useAuth';
import { NotificationSystem } from '@/components/NotificationSystem';
import { NotificationsCenter } from '@/components/NotificationsCenter';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface LayoutProps {
  children: React.ReactNode;
}

interface NavItem {
  label: string;
  path: string;
  icon: typeof LayoutDashboard;
}

const navItems: NavItem[] = [
  { label: 'Dashboard', path: ROUTE_PATHS.DASHBOARD, icon: LayoutDashboard },
  { label: 'Planos & Billing', path: ROUTE_PATHS.COMMERCIAL_PLANS, icon: CreditCard },
  { label: 'Assinatura', path: ROUTE_PATHS.BILLING, icon: CreditCard },
  { label: 'Faturação', path: ROUTE_PATHS.INVOICING, icon: FileText },
  { label: 'Payroll', path: ROUTE_PATHS.PAYROLL, icon: Users },
  { label: 'RH Inteligente', path: ROUTE_PATHS.HR_MANAGEMENT, icon: Brain },
  { label: 'Financeiro', path: ROUTE_PATHS.FINANCE, icon: Wallet },
  { label: 'Planejamento Financeiro', path: ROUTE_PATHS.FINANCIAL_PLANNING, icon: Target },
  { label: 'Finanças Avançadas', path: ROUTE_PATHS.ADVANCED_FINANCE, icon: Brain },
  { label: 'Gestão Empresarial', path: ROUTE_PATHS.BUSINESS_MANAGEMENT, icon: BarChart3 },
  { label: 'Relatórios', path: ROUTE_PATHS.REPORTS, icon: BarChart3 },
  { label: 'Desenvolvedores', path: ROUTE_PATHS.DEVELOPERS, icon: Code },
  { label: 'Marketplace', path: ROUTE_PATHS.MARKETPLACE, icon: Store },
  { label: 'Mobile', path: ROUTE_PATHS.MOBILE, icon: Smartphone },
  { label: 'Configurações', path: ROUTE_PATHS.SETTINGS, icon: Settings },
];

const aiModules: NavItem[] = [
  { label: 'Dashboard IA', path: ROUTE_PATHS.AI_DASHBOARD, icon: Brain },
  { label: 'IA Transversal', path: ROUTE_PATHS.AI_TRANSVERSAL, icon: Bot },
  { label: 'UEBA (Anomalias)', path: ROUTE_PATHS.AI_UEBA, icon: AlertTriangle },
  { label: 'Relatórios IA', path: ROUTE_PATHS.AI_REPORTS, icon: Sparkles },
  { label: 'Decisões IA', path: ROUTE_PATHS.AI_DECISIONS, icon: Target },
  { label: 'Context Engine', path: ROUTE_PATHS.CONTEXT_ENGINE, icon: Activity },
];

const advancedModules: NavItem[] = [
  { label: 'Funcionalidades Avançadas', path: ROUTE_PATHS.ADVANCED_FEATURES, icon: Sparkles },
  { label: 'RH Avançado', path: ROUTE_PATHS.ADVANCED_HR, icon: Upload },
  { label: 'Faturação Avançada', path: ROUTE_PATHS.ADVANCED_INVOICING, icon: Repeat },
];

const iamPamBillingModules: NavItem[] = [
  { label: 'IAM Dashboard', path: ROUTE_PATHS.IAM_DASHBOARD, icon: Shield },
  { label: 'PAM Dashboard', path: ROUTE_PATHS.PAM_DASHBOARD, icon: Key },
  { label: 'Billing Dashboard', path: ROUTE_PATHS.BILLING_DASHBOARD, icon: CreditCard },
  { label: 'RBAC + ABAC', path: ROUTE_PATHS.RBAC_DASHBOARD, icon: Lock },
  { label: 'Status Integrações', path: ROUTE_PATHS.INTEGRATION_STATUS, icon: Plug },
  { label: 'Empresa Inteligente', path: ROUTE_PATHS.SMART_COMPANY, icon: Brain },
];

const newFeaturesModules: NavItem[] = [
  { label: 'QA & Testing', path: ROUTE_PATHS.QA_DASHBOARD, icon: CheckCircle },
  { label: 'RPA (Automação)', path: ROUTE_PATHS.RPA_DASHBOARD, icon: Bot },
  { label: 'Chat com IA', path: ROUTE_PATHS.AI_CHAT, icon: Bot },
];

const completeModules: NavItem[] = [
  { label: 'Marketplace', path: ROUTE_PATHS.MARKETPLACE_COMPLETE, icon: Store },
  { label: 'Métricas Completas', path: ROUTE_PATHS.METRICS_COMPLETE, icon: BarChart3 },
  { label: 'Auditoria Completa', path: ROUTE_PATHS.AUDIT_COMPLETE, icon: Shield },
  { label: 'Integração AGT', path: ROUTE_PATHS.AGT_INTEGRATION, icon: FileText },
  { label: 'Relatórios Avançados', path: ROUTE_PATHS.ADVANCED_REPORTS, icon: BarChart3 },
  { label: 'Monitor de Performance', path: ROUTE_PATHS.PERFORMANCE_MONITOR, icon: Activity },
  { label: 'Melhorias UX/UI', path: ROUTE_PATHS.UX_SHOWCASE, icon: Sparkles },
  { label: 'Animações e Transições', path: ROUTE_PATHS.ANIMATIONS, icon: Zap },
  { label: 'Sistema de Notificações', path: ROUTE_PATHS.NOTIFICATIONS_MANAGEMENT, icon: Bell },
  { label: 'Integrações Externas', path: ROUTE_PATHS.EXTERNAL_INTEGRATIONS, icon: Key },
  { label: 'Segurança & Analytics', path: ROUTE_PATHS.SECURITY_ANALYTICS, icon: Shield },
  { label: 'Módulos Principais', path: ROUTE_PATHS.CORE_MODULES, icon: Package },
  { label: 'Próximos Passos', path: ROUTE_PATHS.ROADMAP, icon: Target },
];
const iamPamItems: NavItem[] = [
  { label: 'Utilizadores', path: ROUTE_PATHS.USERS, icon: Users },
  { label: 'Roles & Permissões', path: ROUTE_PATHS.ROLES, icon: Shield },
  { label: 'Aprovações', path: ROUTE_PATHS.APPROVALS, icon: CheckCircle },
  { label: 'Auditoria', path: ROUTE_PATHS.AUDIT, icon: FileSearch },
  { label: 'Notificações', path: ROUTE_PATHS.NOTIFICATIONS, icon: Bell },
  { label: 'Métricas', path: ROUTE_PATHS.METRICS, icon: BarChart3 },
  { label: 'Segurança Avançada', path: ROUTE_PATHS.ADVANCED_SECURITY, icon: Shield },
];

export function Layout({ children }: LayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [darkMode, setDarkMode] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const { user, profile, signOut } = useAuth();

  useEffect(() => {
    const isDark = localStorage.getItem('darkMode') === 'true';
    setDarkMode(isDark);
    if (isDark) {
      document.documentElement.classList.add('dark');
    }
  }, []);

  const toggleDarkMode = () => {
    const newDarkMode = !darkMode;
    setDarkMode(newDarkMode);
    localStorage.setItem('darkMode', String(newDarkMode));
    if (newDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  };
  
  { label: 'IA Transversal', path: ROUTE_PATHS.AI_TRANSVERSAL, icon: Brain },
  { label: 'UEBA (Anomalias)', path: ROUTE_PATHS.AI_UEBA, icon: AlertTriangle },

  const handleLogout = async () => {
    await signOut();
    navigate(ROUTE_PATHS.LOGIN);
  };

  const getBreadcrumbs = () => {
    const pathSegments = location.pathname.split('/').filter(Boolean);
    if (pathSegments.length === 0) return [{ label: 'Dashboard', path: ROUTE_PATHS.DASHBOARD }];
    
    const currentItem = navItems.find(item => item.path === location.pathname);
    return currentItem ? [{ label: currentItem.label, path: currentItem.path }] : [];
  };

  const breadcrumbs = getBreadcrumbs();

  return (
    <div className="min-h-screen bg-background">
      <aside
        className={cn(
          'fixed left-0 top-0 z-40 h-screen bg-sidebar border-r border-sidebar-border transition-all duration-300 hidden lg:flex flex-col',
          sidebarOpen ? 'w-64' : 'w-20'
        )}
      >
        <div className="flex h-16 items-center justify-between px-4 border-b border-sidebar-border flex-shrink-0">
          {sidebarOpen && (
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
                <span className="text-primary-foreground font-bold text-sm">KC</span>
              </div>
              <span className="font-bold text-sidebar-foreground">KWANZACONTROL</span>
            </div>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="text-sidebar-foreground hover:bg-sidebar-accent"
          >
            <Menu className="h-5 w-5" />
          </Button>
        </div>

        <nav className="flex-1 overflow-y-auto p-4 space-y-2 pb-6 min-h-0">
          {/* Main Navigation */}
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = location.pathname === item.path;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200',
                  isActive
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                    : 'text-sidebar-foreground hover:bg-sidebar-accent/50'
                )}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                {sidebarOpen && <span>{item.label}</span>}
              </NavLink>
            );
          })}

          {/* AI Modules Section */}
          {sidebarOpen && (
            <div className="pt-4 mt-4 border-t border-sidebar-border">
              <p className="px-3 text-xs font-semibold text-sidebar-foreground/60 uppercase tracking-wider mb-2">
                IA Transversal
              </p>
            </div>
          )}
          {aiModules.map((item) => {
            const Icon = item.icon;
            const isActive = location.pathname === item.path;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200',
                  isActive
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                    : 'text-sidebar-foreground hover:bg-sidebar-accent/50'
                )}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                {sidebarOpen && <span>{item.label}</span>}
              </NavLink>
            );
          })}

          {/* Advanced Modules Section */}
          {sidebarOpen && (
            <div className="pt-4 mt-4 border-t border-sidebar-border">
              <p className="px-3 text-xs font-semibold text-sidebar-foreground/60 uppercase tracking-wider mb-2">
                Módulos Avançados
              </p>
            </div>
          )}
          {advancedModules.map((item) => {
            const Icon = item.icon;
            const isActive = location.pathname === item.path;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200',
                  isActive
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                    : 'text-sidebar-foreground hover:bg-sidebar-accent/50'
                )}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                {sidebarOpen && <span>{item.label}</span>}
              </NavLink>
            );
          })}

          {/* IAM + PAM + BILLING Section */}
          {sidebarOpen && (
            <div className="pt-4 mt-4 border-t border-sidebar-border">
              <p className="px-3 text-xs font-semibold text-sidebar-foreground/60 uppercase tracking-wider mb-2">
                IAM + PAM + BILLING
              </p>
            </div>
          )}
          {iamPamBillingModules.map((item) => {
            const Icon = item.icon;
            const isActive = location.pathname === item.path;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200',
                  isActive
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
                    : 'text-sidebar-foreground hover:bg-sidebar-accent/50'
                )}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                {sidebarOpen && <span>{item.label}</span>}
              </NavLink>
            );
          })}
        </nav>
      </aside>

      <div
        className={cn(
          'transition-all duration-300 hidden lg:block',
          sidebarOpen ? 'ml-64' : 'ml-20'
        )}
      >
        <header className="sticky top-0 z-30 h-16 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60 border-b border-border">
          <div className="flex h-full items-center justify-between px-6">
            <div className="flex items-center gap-6">
              {/* Breadcrumbs */}
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                {breadcrumbs.map((crumb, index) => (
                  <div key={crumb.path} className="flex items-center gap-2">
                    {index > 0 && <ChevronRight className="h-4 w-4" />}
                    <span className={index === breadcrumbs.length - 1 ? 'text-foreground font-medium' : ''}>
                      {crumb.label}
                    </span>
                  </div>
                ))}
              </div>

              {/* Módulos Completos Dropdown */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" className="flex items-center gap-2 text-sm">
                    <Sparkles className="h-4 w-4" />
                    <span>Módulos Completos</span>
                    <ChevronRight className="h-4 w-4 rotate-90" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-56">
                  <DropdownMenuLabel>Módulos Completos</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  {completeModules.map((item) => {
                    const Icon = item.icon;
                    return (
                      <DropdownMenuItem key={item.path} onClick={() => navigate(item.path)}>
                        <Icon className="mr-2 h-4 w-4" />
                        <span>{item.label}</span>
                      </DropdownMenuItem>
                    );
                  })}
                </DropdownMenuContent>
              </DropdownMenu>

              {/* Novas Funcionalidades Dropdown */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" className="flex items-center gap-2 text-sm">
                    <Target className="h-4 w-4" />
                    <span>Novas Funcionalidades</span>
                    <ChevronRight className="h-4 w-4 rotate-90" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-56">
                  <DropdownMenuLabel>Novas Funcionalidades</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  {newFeaturesModules.map((item) => {
                    const Icon = item.icon;
                    return (
                      <DropdownMenuItem key={item.path} onClick={() => navigate(item.path)}>
                        <Icon className="mr-2 h-4 w-4" />
                        <span>{item.label}</span>
                      </DropdownMenuItem>
                    );
                  })}
                </DropdownMenuContent>
              </DropdownMenu>

              {/* IAM & PAM Dropdown */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" className="flex items-center gap-2 text-sm">
                    <Shield className="h-4 w-4" />
                    <span>IAM & PAM</span>
                    <ChevronRight className="h-4 w-4 rotate-90" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-56">
                  <DropdownMenuLabel>IAM & PAM</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  {iamPamItems.map((item) => {
                    const Icon = item.icon;
                    return (
                      <DropdownMenuItem key={item.path} onClick={() => navigate(item.path)}>
                        <Icon className="mr-2 h-4 w-4" />
                        <span>{item.label}</span>
                      </DropdownMenuItem>
                    );
                  })}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <div className="flex items-center gap-3">
              <Button
                variant="ghost"
                size="icon"
                onClick={toggleDarkMode}
                className="text-muted-foreground hover:text-foreground"
              >
                {darkMode ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
              </Button>

              <NotificationsCenter />

              <NotificationSystem />

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" className="flex items-center gap-2 px-2">
                    <Avatar className="h-8 w-8">
                      <AvatarImage src={profile?.avatar_url || undefined} alt={profile?.full_name} />
                      <AvatarFallback>{profile?.full_name?.charAt(0) || 'U'}</AvatarFallback>
                    </Avatar>
                    <div className="hidden md:block text-left">
                      <p className="text-sm font-medium">{profile?.full_name}</p>
                      <p className="text-xs text-muted-foreground">{profile?.role}</p>
                    </div>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-56">
                  <DropdownMenuLabel>Minha Conta</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => navigate(ROUTE_PATHS.SETTINGS)}>
                    <User className="mr-2 h-4 w-4" />
                    <span>Perfil</span>
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => navigate(ROUTE_PATHS.SETTINGS)}>
                    <Settings className="mr-2 h-4 w-4" />
                    <span>Configurações</span>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={handleLogout} className="text-destructive">
                    <LogOut className="mr-2 h-4 w-4" />
                    <span>Sair</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </header>

        <main className="p-6">{children}</main>

        <footer className="border-t border-border py-6 px-6 mt-12">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4 text-sm text-muted-foreground">
            <p>© 2026 KWANZACONTROL. Todos os direitos reservados.</p>
            <div className="flex items-center gap-4">
              <a href="#" className="hover:text-foreground transition-colors">Termos de Uso</a>
              <a href="#" className="hover:text-foreground transition-colors">Privacidade</a>
              <a href="#" className="hover:text-foreground transition-colors">Suporte</a>
            </div>
          </div>
        </footer>
      </div>

      <div className="lg:hidden">
        <header className="sticky top-0 z-30 h-16 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60 border-b border-border">
          <div className="flex h-full items-center justify-between px-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
                <span className="text-primary-foreground font-bold text-sm">KC</span>
              </div>
              <span className="font-bold text-foreground">KWANZACONTROL</span>
            </div>

            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                onClick={toggleDarkMode}
                className="text-muted-foreground hover:text-foreground"
              >
                {darkMode ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              >
                {mobileMenuOpen ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
              </Button>
            </div>
          </div>
        </header>

        {mobileMenuOpen && (
          <div className="fixed inset-0 top-16 z-40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/90 overflow-y-auto">
            <nav className="p-4 space-y-2 pb-32">
              {/* Main Navigation */}
              {navItems.map((item) => {
                const Icon = item.icon;
                const isActive = location.pathname === item.path;
                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    onClick={() => setMobileMenuOpen(false)}
                    className={cn(
                      'flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-200',
                      isActive
                        ? 'bg-primary text-primary-foreground font-medium'
                        : 'text-foreground hover:bg-muted'
                    )}
                  >
                    <Icon className="h-5 w-5" />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}

              {/* IAM & PAM Section */}
              <div className="pt-4 mt-4 border-t border-border">
                <p className="px-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                  IAM & PAM
                </p>
              </div>
              {iamPamItems.map((item) => {
                const Icon = item.icon;
                const isActive = location.pathname === item.path;
                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    onClick={() => setMobileMenuOpen(false)}
                    className={cn(
                      'flex items-center gap-3 px-4 py-3 rounded-lg transition-all duration-200',
                      isActive
                        ? 'bg-primary text-primary-foreground font-medium'
                        : 'text-foreground hover:bg-muted'
                    )}
                  >
                    <Icon className="h-5 w-5" />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
              <div className="pt-4 border-t border-border">
                <Button
                  variant="ghost"
                  className="w-full justify-start text-destructive hover:text-destructive hover:bg-destructive/10"
                  onClick={handleLogout}
                >
                  <LogOut className="mr-2 h-5 w-5" />
                  <span>Sair</span>
                </Button>
              </div>
            </nav>
          </div>
        )}

        <main className="p-4 pb-20">{children}</main>

        <nav className="fixed bottom-0 left-0 right-0 z-30 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60 border-t border-border">
          <div className="flex items-center justify-around py-2">
            {navItems.slice(0, 5).map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.path;
              return (
                <NavLink
                  key={item.path}
                  to={item.path}
                  className={cn(
                    'flex flex-col items-center gap-1 px-3 py-2 rounded-lg transition-all duration-200 min-w-[44px]',
                    isActive
                      ? 'text-primary'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  <Icon className="h-5 w-5" />
                  <span className="text-xs">{item.label}</span>
                </NavLink>
              );
            })}
          </div>
        </nav>

        <footer className="border-t border-border py-6 px-4 mt-12 mb-20">
          <div className="text-center text-sm text-muted-foreground">
            <p>© 2026 KWANZACONTROL</p>
          </div>
        </footer>
      </div>
    </div>
  );
}