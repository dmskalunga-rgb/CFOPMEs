// Loading Skeleton Components
import { Card, CardContent, CardHeader } from '@/components/ui/card';

export function TableSkeleton({ rows = 5, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-4">
          {Array.from({ length: columns }).map((_, j) => (
            <div key={j} className="h-12 bg-muted animate-pulse rounded flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function CardSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      {Array.from({ length: count }).map((_, i) => (
        <Card key={i}>
          <CardHeader className="space-y-2">
            <div className="h-4 bg-muted animate-pulse rounded w-1/2" />
            <div className="h-8 bg-muted animate-pulse rounded w-3/4" />
          </CardHeader>
        </Card>
      ))}
    </div>
  );
}

export function ListSkeleton({ items = 5 }: { items?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: items }).map((_, i) => (
        <div key={i} className="flex items-center gap-4 p-4 border rounded-lg">
          <div className="h-12 w-12 bg-muted animate-pulse rounded-full" />
          <div className="flex-1 space-y-2">
            <div className="h-4 bg-muted animate-pulse rounded w-1/3" />
            <div className="h-3 bg-muted animate-pulse rounded w-1/2" />
          </div>
          <div className="h-8 w-20 bg-muted animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}

export function FormSkeleton() {
  return (
    <div className="space-y-4">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="space-y-2">
          <div className="h-4 bg-muted animate-pulse rounded w-1/4" />
          <div className="h-10 bg-muted animate-pulse rounded" />
        </div>
      ))}
      <div className="flex gap-2">
        <div className="h-10 bg-muted animate-pulse rounded w-24" />
        <div className="h-10 bg-muted animate-pulse rounded w-24" />
      </div>
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div className="space-y-2">
          <div className="h-8 bg-muted animate-pulse rounded w-48" />
          <div className="h-4 bg-muted animate-pulse rounded w-64" />
        </div>
        <div className="h-10 bg-muted animate-pulse rounded w-32" />
      </div>
      <CardSkeleton />
      <Card>
        <CardHeader>
          <div className="h-6 bg-muted animate-pulse rounded w-32" />
        </CardHeader>
        <CardContent>
          <TableSkeleton />
        </CardContent>
      </Card>
    </div>
  );
}
