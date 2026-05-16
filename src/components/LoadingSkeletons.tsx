// =====================================================
// KWANZACONTROL - Loading Skeleton Components
// Componentes de Loading para melhor UX
// Data: 2026-04-04
// =====================================================

import { Card } from '@/components/ui/card';

export function DashboardSkeleton() {
  return (
    <div className="p-8 space-y-6 animate-pulse">
      {/* Header Skeleton */}
      <div className="space-y-2">
        <div className="h-8 bg-muted rounded w-64"></div>
        <div className="h-4 bg-muted rounded w-96"></div>
      </div>

      {/* Stats Cards Skeleton */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i} className="p-6">
            <div className="space-y-3">
              <div className="h-4 bg-muted rounded w-24"></div>
              <div className="h-8 bg-muted rounded w-16"></div>
            </div>
          </Card>
        ))}
      </div>

      {/* Chart Skeleton */}
      <Card className="p-6">
        <div className="h-64 bg-muted rounded"></div>
      </Card>
    </div>
  );
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-3 animate-pulse">
      {Array.from({ length: rows }).map((_, i) => (
        <Card key={i} className="p-4">
          <div className="flex items-center gap-4">
            <div className="h-10 w-10 bg-muted rounded-full"></div>
            <div className="flex-1 space-y-2">
              <div className="h-4 bg-muted rounded w-3/4"></div>
              <div className="h-3 bg-muted rounded w-1/2"></div>
            </div>
            <div className="h-8 w-24 bg-muted rounded"></div>
          </div>
        </Card>
      ))}
    </div>
  );
}

export function CardSkeleton() {
  return (
    <Card className="p-6 animate-pulse">
      <div className="space-y-4">
        <div className="h-6 bg-muted rounded w-1/2"></div>
        <div className="h-4 bg-muted rounded w-3/4"></div>
        <div className="h-4 bg-muted rounded w-2/3"></div>
        <div className="flex gap-2">
          <div className="h-10 bg-muted rounded w-24"></div>
          <div className="h-10 bg-muted rounded w-24"></div>
        </div>
      </div>
    </Card>
  );
}

export function FormSkeleton() {
  return (
    <div className="space-y-4 animate-pulse">
      {[1, 2, 3].map((i) => (
        <div key={i} className="space-y-2">
          <div className="h-4 bg-muted rounded w-24"></div>
          <div className="h-10 bg-muted rounded w-full"></div>
        </div>
      ))}
      <div className="h-10 bg-muted rounded w-32"></div>
    </div>
  );
}

export default {
  DashboardSkeleton,
  TableSkeleton,
  CardSkeleton,
  FormSkeleton,
};
