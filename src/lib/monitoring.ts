// Error Tracking and Monitoring Service
// Integrates with Sentry-like error tracking

interface ErrorContext {
  user?: {
    id: string;
    email: string;
    role: string;
  };
  tags?: Record<string, string>;
  extra?: Record<string, any>;
}

interface PerformanceMetric {
  name: string;
  value: number;
  unit: 'ms' | 'bytes' | 'count';
  timestamp: number;
  tags?: Record<string, string>;
}

class MonitoringService {
  private isInitialized = false;
  private userId: string | null = null;
  private sessionId: string;

  constructor() {
    this.sessionId = this.generateSessionId();
  }

  initialize(config: { dsn?: string; environment: string; release: string }) {
    if (this.isInitialized) return;

    console.log('[Monitoring] Initializing...', config);

    // Initialize error tracking (Sentry-like)
    this.setupErrorTracking();
    this.setupPerformanceMonitoring();
    this.setupUserTracking();

    this.isInitialized = true;
    console.log('[Monitoring] Initialized successfully');
  }

  private setupErrorTracking() {
    // Global error handler
    window.addEventListener('error', (event) => {
      this.captureError(event.error, {
        tags: { type: 'uncaught_error' },
        extra: {
          message: event.message,
          filename: event.filename,
          lineno: event.lineno,
          colno: event.colno,
        },
      });
    });

    // Unhandled promise rejection handler
    window.addEventListener('unhandledrejection', (event) => {
      this.captureError(new Error(event.reason), {
        tags: { type: 'unhandled_rejection' },
        extra: { reason: event.reason },
      });
    });
  }

  private setupPerformanceMonitoring() {
    // Monitor page load performance
    if ('performance' in window) {
      window.addEventListener('load', () => {
        setTimeout(() => {
          const perfData = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
          
          if (perfData) {
            this.trackPerformance({
              name: 'page_load',
              value: perfData.loadEventEnd - perfData.fetchStart,
              unit: 'ms',
              timestamp: Date.now(),
              tags: { page: window.location.pathname },
            });

            this.trackPerformance({
              name: 'dom_content_loaded',
              value: perfData.domContentLoadedEventEnd - perfData.fetchStart,
              unit: 'ms',
              timestamp: Date.now(),
              tags: { page: window.location.pathname },
            });

            this.trackPerformance({
              name: 'first_paint',
              value: perfData.responseStart - perfData.fetchStart,
              unit: 'ms',
              timestamp: Date.now(),
              tags: { page: window.location.pathname },
            });
          }
        }, 0);
      });
    }

    // Monitor resource loading
    if ('PerformanceObserver' in window) {
      try {
        const observer = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (entry.entryType === 'resource') {
              const resourceEntry = entry as PerformanceResourceTiming;
              if (resourceEntry.duration > 1000) {
                // Log slow resources (> 1s)
                this.trackPerformance({
                  name: 'slow_resource',
                  value: resourceEntry.duration,
                  unit: 'ms',
                  timestamp: Date.now(),
                  tags: {
                    resource: resourceEntry.name,
                    type: resourceEntry.initiatorType,
                  },
                });
              }
            }
          }
        });
        observer.observe({ entryTypes: ['resource'] });
      } catch (e) {
        console.warn('[Monitoring] PerformanceObserver not supported');
      }
    }
  }

  private setupUserTracking() {
    // Track user interactions
    document.addEventListener('click', (event) => {
      const target = event.target as HTMLElement;
      if (target.tagName === 'BUTTON' || target.tagName === 'A') {
        this.trackEvent('user_interaction', {
          element: target.tagName,
          text: target.textContent?.slice(0, 50),
          page: window.location.pathname,
        });
      }
    });
  }

  setUser(user: { id: string; email: string; role: string }) {
    this.userId = user.id;
    console.log('[Monitoring] User set:', user.email);
  }

  clearUser() {
    this.userId = null;
    console.log('[Monitoring] User cleared');
  }

  captureError(error: Error, context?: ErrorContext) {
    const errorData = {
      message: error.message,
      stack: error.stack,
      name: error.name,
      timestamp: new Date().toISOString(),
      sessionId: this.sessionId,
      userId: this.userId,
      url: window.location.href,
      userAgent: navigator.userAgent,
      ...context,
    };

    console.error('[Monitoring] Error captured:', errorData);

    // Send to backend/monitoring service
    this.sendToBackend('error', errorData);
  }

  captureMessage(message: string, level: 'info' | 'warning' | 'error' = 'info', context?: ErrorContext) {
    const messageData = {
      message,
      level,
      timestamp: new Date().toISOString(),
      sessionId: this.sessionId,
      userId: this.userId,
      url: window.location.href,
      ...context,
    };

    console.log(`[Monitoring] Message captured [${level}]:`, messageData);

    this.sendToBackend('message', messageData);
  }

  trackPerformance(metric: PerformanceMetric) {
    console.log('[Monitoring] Performance metric:', metric);
    this.sendToBackend('performance', metric);
  }

  trackEvent(eventName: string, properties?: Record<string, any>) {
    const eventData = {
      event: eventName,
      properties,
      timestamp: new Date().toISOString(),
      sessionId: this.sessionId,
      userId: this.userId,
      page: window.location.pathname,
    };

    console.log('[Monitoring] Event tracked:', eventData);
    this.sendToBackend('event', eventData);
  }

  private async sendToBackend(type: string, data: any) {
    try {
      // In production, send to your monitoring backend
      // For now, we'll store in localStorage for demo
      const key = `monitoring_${type}_${Date.now()}`;
      localStorage.setItem(key, JSON.stringify(data));

      // Clean up old entries (keep last 100)
      const keys = Object.keys(localStorage).filter((k) => k.startsWith('monitoring_'));
      if (keys.length > 100) {
        keys.slice(0, keys.length - 100).forEach((k) => localStorage.removeItem(k));
      }
    } catch (error) {
      console.error('[Monitoring] Failed to send data:', error);
    }
  }

  private generateSessionId(): string {
    return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  }

  // Get monitoring dashboard data
  getDashboardData() {
    const keys = Object.keys(localStorage).filter((k) => k.startsWith('monitoring_'));
    const data = keys.map((key) => {
      try {
        return JSON.parse(localStorage.getItem(key) || '{}');
      } catch {
        return null;
      }
    }).filter(Boolean);

    const errors = data.filter((d) => d.message && d.stack);
    const events = data.filter((d) => d.event);
    const performance = data.filter((d) => d.name && d.value);

    return {
      errors: {
        total: errors.length,
        recent: errors.slice(-10),
      },
      events: {
        total: events.length,
        recent: events.slice(-10),
      },
      performance: {
        total: performance.length,
        recent: performance.slice(-10),
        averages: this.calculateAverages(performance),
      },
    };
  }

  private calculateAverages(metrics: PerformanceMetric[]) {
    const grouped = metrics.reduce((acc, metric) => {
      if (!acc[metric.name]) {
        acc[metric.name] = [];
      }
      acc[metric.name].push(metric.value);
      return acc;
    }, {} as Record<string, number[]>);

    return Object.entries(grouped).map(([name, values]) => ({
      name,
      average: values.reduce((a, b) => a + b, 0) / values.length,
      min: Math.min(...values),
      max: Math.max(...values),
      count: values.length,
    }));
  }
}

export const monitoring = new MonitoringService();

// Initialize monitoring
if (typeof window !== 'undefined') {
  monitoring.initialize({
    environment: import.meta.env.MODE,
    release: import.meta.env.VITE_APP_VERSION || '1.0.0',
  });
}
