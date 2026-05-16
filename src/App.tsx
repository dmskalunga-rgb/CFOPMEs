import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ToastProvider } from "@/lib/toast-provider";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter, Routes, Route, Navigate } from "react-router-dom";
import { MotionConfig } from "framer-motion";
import { useAuth, AuthProvider } from "@/hooks/useAuth";
import { ROUTE_PATHS } from "@/lib/index";
import { FeedbackWidget } from "@/components/FeedbackWidget";
import { PageLoader } from "@/components/LoadingStates";
import { PWAInstallPrompt, useServiceWorker } from "@/components/PWAInstallPrompt";
import { monitoring } from "@/lib/monitoring";
import { useEffect, useState, lazy, Suspense } from "react";

// Lazy load pages for better performance
const Home = lazy(() => import("@/pages/Home"));
const LoginWith2FA = lazy(() => import("@/pages/LoginWith2FA"));
const Login = lazy(() => import("@/pages/Login"));
const Register = lazy(() => import("@/pages/Register"));
const ResetPassword = lazy(() => import("@/pages/ResetPassword"));
const Onboarding = lazy(() => import("@/pages/Onboarding"));
// const Billing = lazy(() => import("@/pages/Billing")); // Removido - usar BillingDashboardPage
const Dashboard = lazy(() => import("@/pages/DashboardIntegrated"));
const Invoicing = lazy(() => import("@/pages/Invoicing"));
const Payroll = lazy(() => import("@/pages/Payroll"));
const HRManagement = lazy(() => import("@/pages/HRManagement"));
const Finance = lazy(() => import("@/pages/Finance"));
const Reports = lazy(() => import("@/pages/Reports"));
const Settings = lazy(() => import("@/pages/Settings"));
const Users = lazy(() => import("@/pages/Users"));
const Roles = lazy(() => import("@/pages/Roles"));
const Approvals = lazy(() => import("@/pages/ApprovalsIntegrated"));
// const Audit = lazy(() => import("@/pages/Audit")); // Removido - usar AuditComplete
const Notifications = lazy(() => import("@/pages/NotificationsIntegrated"));
const Metrics = lazy(() => import("@/pages/Metrics"));
const AdvancedSecurity = lazy(() => import("@/pages/AdvancedSecurity"));
const AdvancedFinance = lazy(() => import("@/pages/AdvancedFinance"));
const BusinessManagement = lazy(() => import("@/pages/BusinessManagement"));
const Developers = lazy(() => import("@/pages/Developers"));
// Marketplace removed - using MarketplaceComplete instead
const FinancialPlanning = lazy(() => import("@/pages/FinancialPlanning"));
const Mobile = lazy(() => import("@/pages/Mobile"));
const AIDashboard = lazy(() => import("@/pages/AIDashboard"));
const AIUeba = lazy(() => import("@/pages/AIUeba"));
const AIReports = lazy(() => import("@/pages/AIReports"));
const AIDecisions = lazy(() => import("@/pages/AIDecisions"));
const ContextEngine = lazy(() => import("@/pages/ContextEngine"));
const AdvancedHR = lazy(() => import("@/pages/AdvancedHR"));
const AdvancedInvoicing = lazy(() => import("@/pages/AdvancedInvoicing"));
const AdvancedFeatures = lazy(() => import("@/pages/AdvancedFeatures"));
const AITransversal = lazy(() => import("@/pages/AITransversal"));
const IAMDashboard = lazy(() => import("@/pages/IAMDashboardPage"));
const PAMDashboard = lazy(() => import("@/pages/PAMDashboardPage"));
const BillingDashboard = lazy(() => import("@/pages/BillingDashboardPage"));
const RBACDashboard = lazy(() => import("@/pages/RBACDashboardPage"));
const IntegrationStatus = lazy(() => import("@/pages/IntegrationStatus"));
const SmartCompanyDashboard = lazy(() => import("@/pages/SmartCompanyDashboard"));
const QADashboard = lazy(() => import("@/pages/QADashboard"));
const RPADashboard = lazy(() => import("@/pages/RPADashboard"));
const AIChat = lazy(() => import("@/pages/AIChat"));
const MarketplaceComplete = lazy(() => import("@/pages/MarketplaceComplete"));
const MetricsComplete = lazy(() => import("@/pages/MetricsComplete"));
const AuditComplete = lazy(() => import("@/pages/AuditComplete"));
const CommercialPlansPage = lazy(() => import("@/pages/CommercialPlansPage"));
const RoadmapPage = lazy(() => import("@/pages/RoadmapPage"));
const AGTIntegrationPage = lazy(() => import("@/pages/AGTIntegrationPage"));
const AdvancedReportsPage = lazy(() => import("@/pages/AdvancedReportsPage"));
const ReportViewerPage = lazy(() => import("@/pages/ReportViewerPage"));
const PerformanceMonitorPage = lazy(() => import("@/pages/PerformanceMonitorPage"));
const UXShowcasePage = lazy(() => import("@/pages/UXShowcasePage"));
const AnimationsPage = lazy(() => import("@/pages/AnimationsPage"));
const NotificationsManagementPage = lazy(() => import("@/pages/NotificationsManagementPage"));
const ExternalIntegrationsPage = lazy(() => import("@/pages/ExternalIntegrationsPage"));
const SecurityAnalyticsPage = lazy(() => import("@/pages/SecurityAnalyticsPage"));
const CoreModulesPage = lazy(() => import("@/pages/CoreModulesPage"));
const ProductsReal = lazy(() => import("@/pages/ProductsReal"));
const CustomersReal = lazy(() => import("@/pages/CustomersReal"));
const FinanceReal = lazy(() => import("@/pages/FinanceReal"));
const DashboardConsolidated = lazy(() => import("@/pages/DashboardConsolidated"));
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      gcTime: 1000 * 60 * 30, // 30 minutes (formerly cacheTime)
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
      retry: 1,
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000),
    },
    mutations: {
      retry: 1,
      retryDelay: 1000,
    },
  },
});

interface ProtectedRouteProps {
  children: React.ReactNode;
}

const ProtectedRoute = ({ children }: ProtectedRouteProps) => {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-background">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full animate-spin" />
          <p className="text-muted-foreground">A carregar...</p>
        </div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to={ROUTE_PATHS.LOGIN} replace />;
  }

  return <>{children}</>;
};

const ThemeProvider = ({ children }: { children: React.ReactNode }) => {
  const [theme, setTheme] = useState<'light' | 'dark'>('light');

  useEffect(() => {
    const stored = localStorage.getItem('kwanzacontrol_theme');
    if (stored === 'dark' || stored === 'light') {
      setTheme(stored);
    } else {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      setTheme(prefersDark ? 'dark' : 'light');
    }
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.classList.add('dark');
    } else {
      root.classList.remove('dark');
    }
    localStorage.setItem('kwanzacontrol_theme', theme);
  }, [theme]);

  return <>{children}</>;
};

// Component to track user for monitoring (must be inside AuthProvider)
const MonitoringTracker = (): null => {
  const { user } = useAuth();
  
  useEffect(() => {
    if (user) {
      monitoring.setUser({
        id: user.id,
        email: user.email,
        role: user.role || 'user',
      });
    } else {
      monitoring.clearUser();
    }
  }, [user]);
  
  return null;
};

const App = () => {
  // Initialize service worker
  useServiceWorker();

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MonitoringTracker />
        <ThemeProvider>
          <ToastProvider>
            <TooltipProvider>
              <MotionConfig reducedMotion="user">
                <Toaster />
                <Sonner />
            <HashRouter>
              <Suspense fallback={<PageLoader message="A carregar página..." />}>
              <Routes>
                <Route path={ROUTE_PATHS.HOME} element={<Home />} />
                <Route path={ROUTE_PATHS.LOGIN} element={<Login />} />
                <Route path={ROUTE_PATHS.REGISTER} element={<Register />} />
                <Route path={ROUTE_PATHS.RESET_PASSWORD} element={<ResetPassword />} />
                <Route path={ROUTE_PATHS.ONBOARDING} element={<ProtectedRoute><Onboarding /></ProtectedRoute>} />
                {/* <Route path={ROUTE_PATHS.BILLING} element={<ProtectedRoute><Billing /></ProtectedRoute>} /> */}
                <Route path="/login-2fa" element={<LoginWith2FA />} />
                <Route
                  path={ROUTE_PATHS.DASHBOARD}
                  element={
                    <ProtectedRoute>
                      <Dashboard />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.INVOICING}
                  element={
                    <ProtectedRoute>
                      <Invoicing />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.PAYROLL}
                  element={
                    <ProtectedRoute>
                      <Payroll />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.HR_MANAGEMENT}
                  element={
                    <ProtectedRoute>
                      <HRManagement />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.FINANCE}
                  element={
                    <ProtectedRoute>
                      <Finance />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.REPORTS}
                  element={
                    <ProtectedRoute>
                      <Reports />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.SETTINGS}
                  element={
                    <ProtectedRoute>
                      <Settings />
                    </ProtectedRoute>
                  }
                />
                {/* IAM & PAM Routes */}
                <Route
                  path={ROUTE_PATHS.USERS}
                  element={
                    <ProtectedRoute>
                      <Users />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ROLES}
                  element={
                    <ProtectedRoute>
                      <Roles />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.APPROVALS}
                  element={
                    <ProtectedRoute>
                      <Approvals />
                    </ProtectedRoute>
                  }
                />
                {/* Audit route removed - use AuditComplete instead */}
                {/* <Route
                  path={ROUTE_PATHS.AUDIT}
                  element={
                    <ProtectedRoute>
                      <Audit />
                    </ProtectedRoute>
                  }
                /> */}
                <Route
                  path={ROUTE_PATHS.NOTIFICATIONS}
                  element={
                    <ProtectedRoute>
                      <Notifications />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.METRICS}
                  element={
                    <ProtectedRoute>
                      <Metrics />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ADVANCED_SECURITY}
                  element={
                    <ProtectedRoute>
                      <AdvancedSecurity />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ADVANCED_FINANCE}
                  element={
                    <ProtectedRoute>
                      <AdvancedFinance />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.BUSINESS_MANAGEMENT}
                  element={
                    <ProtectedRoute>
                      <BusinessManagement />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.DEVELOPERS}
                  element={
                    <ProtectedRoute>
                      <Developers />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.MARKETPLACE}
                  element={
                    <ProtectedRoute>
                      <MarketplaceComplete />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.FINANCIAL_PLANNING}
                  element={
                    <ProtectedRoute>
                      <FinancialPlanning />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.MOBILE}
                  element={
                    <ProtectedRoute>
                      <Mobile />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.AI_DASHBOARD}
                  element={
                    <ProtectedRoute>
                      <AIDashboard />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.AI_UEBA}
                  element={
                    <ProtectedRoute>
                      <AIUeba />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.AI_REPORTS}
                  element={
                    <ProtectedRoute>
                      <AIReports />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.AI_DECISIONS}
                  element={
                    <ProtectedRoute>
                      <AIDecisions />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.CONTEXT_ENGINE}
                  element={
                    <ProtectedRoute>
                      <ContextEngine />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ADVANCED_HR}
                  element={
                    <ProtectedRoute>
                      <AdvancedHR />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ADVANCED_INVOICING}
                  element={
                    <ProtectedRoute>
                      <AdvancedInvoicing />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ADVANCED_FEATURES}
                  element={
                    <ProtectedRoute>
                      <AdvancedFeatures />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.AI_TRANSVERSAL}
                  element={
                    <ProtectedRoute>
                      <AITransversal />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.IAM_DASHBOARD}
                  element={
                    <ProtectedRoute>
                      <IAMDashboard />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.PAM_DASHBOARD}
                  element={
                    <ProtectedRoute>
                      <PAMDashboard />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.BILLING_DASHBOARD}
                  element={
                    <ProtectedRoute>
                      <BillingDashboard />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.RBAC_DASHBOARD}
                  element={
                    <ProtectedRoute>
                      <RBACDashboard />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.INTEGRATION_STATUS}
                  element={
                    <ProtectedRoute>
                      <IntegrationStatus />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.SMART_COMPANY}
                  element={
                    <ProtectedRoute>
                      <SmartCompanyDashboard />
                    </ProtectedRoute>
                  }
                />
                {/* New Features Routes */}
                <Route
                  path={ROUTE_PATHS.QA_DASHBOARD}
                  element={
                    <ProtectedRoute>
                      <QADashboard />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.RPA_DASHBOARD}
                  element={
                    <ProtectedRoute>
                      <RPADashboard />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.AI_CHAT}
                  element={
                    <ProtectedRoute>
                      <AIChat />
                    </ProtectedRoute>
                  }
                />
                {/* Complete Modules Routes */}
                <Route
                  path={ROUTE_PATHS.MARKETPLACE_COMPLETE}
                  element={
                    <ProtectedRoute>
                      <MarketplaceComplete />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.METRICS_COMPLETE}
                  element={
                    <ProtectedRoute>
                      <MetricsComplete />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.AUDIT_COMPLETE}
                  element={
                    <ProtectedRoute>
                      <AuditComplete />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.COMMERCIAL_PLANS}
                  element={
                    <ProtectedRoute>
                      <CommercialPlansPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ROADMAP}
                  element={
                    <ProtectedRoute>
                      <RoadmapPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.AGT_INTEGRATION}
                  element={
                    <ProtectedRoute>
                      <AGTIntegrationPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ADVANCED_REPORTS}
                  element={
                    <ProtectedRoute>
                      <AdvancedReportsPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.REPORT_VIEWER}
                  element={
                    <ProtectedRoute>
                      <ReportViewerPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.PERFORMANCE_MONITOR}
                  element={
                    <ProtectedRoute>
                      <PerformanceMonitorPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.UX_SHOWCASE}
                  element={
                    <ProtectedRoute>
                      <UXShowcasePage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.ANIMATIONS}
                  element={
                    <ProtectedRoute>
                      <AnimationsPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.NOTIFICATIONS_MANAGEMENT}
                  element={
                    <ProtectedRoute>
                      <NotificationsManagementPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.EXTERNAL_INTEGRATIONS}
                  element={
                    <ProtectedRoute>
                      <ExternalIntegrationsPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.SECURITY_ANALYTICS}
                  element={
                    <ProtectedRoute>
                      <SecurityAnalyticsPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path={ROUTE_PATHS.CORE_MODULES}
                  element={
                    <ProtectedRoute>
                      <CoreModulesPage />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/products-real"
                  element={<ProtectedRoute><ProductsReal /></ProtectedRoute>}
                />
                <Route
                  path="/customers-real"
                  element={<ProtectedRoute><CustomersReal /></ProtectedRoute>}
                />
                <Route
                  path="/finance-real"
                  element={<ProtectedRoute><FinanceReal /></ProtectedRoute>}
                />
                <Route
                  path="/dashboard-consolidated"
                  element={<ProtectedRoute><DashboardConsolidated /></ProtectedRoute>}
                />
                <Route
                  path="*"
                  element={
                    <ProtectedRoute>
                      <SmartCompanyDashboard />
                    </ProtectedRoute>
                  }
                />
                <Route path="*" element={<Navigate to={ROUTE_PATHS.HOME} replace />} />
              </Routes>
              </Suspense>
              <FeedbackWidget />
              <PWAInstallPrompt />
            </HashRouter>
            </MotionConfig>
          </TooltipProvider>
        </ToastProvider>
      </ThemeProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
};

export default App;