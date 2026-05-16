// Chart Components using Recharts
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';

// ============================================
// LINE CHART
// ============================================
interface LineChartData {
  name: string;
  [key: string]: string | number;
}

interface CustomLineChartProps {
  data: LineChartData[];
  lines: { dataKey: string; stroke: string; name: string }[];
  title?: string;
  description?: string;
  height?: number;
}

export function CustomLineChart({
  data,
  lines,
  title,
  description,
  height = 300,
}: CustomLineChartProps) {
  return (
    <Card>
      {(title || description) && (
        <CardHeader>
          {title && <CardTitle>{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <CardContent>
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Legend />
            {lines.map((line) => (
              <Line
                key={line.dataKey}
                type="monotone"
                dataKey={line.dataKey}
                stroke={line.stroke}
                name={line.name}
                strokeWidth={2}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

// ============================================
// BAR CHART
// ============================================
interface BarChartData {
  name: string;
  [key: string]: string | number;
}

interface CustomBarChartProps {
  data: BarChartData[];
  bars: { dataKey: string; fill: string; name: string }[];
  title?: string;
  description?: string;
  height?: number;
}

export function CustomBarChart({
  data,
  bars,
  title,
  description,
  height = 300,
}: CustomBarChartProps) {
  return (
    <Card>
      {(title || description) && (
        <CardHeader>
          {title && <CardTitle>{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <CardContent>
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Legend />
            {bars.map((bar) => (
              <Bar key={bar.dataKey} dataKey={bar.dataKey} fill={bar.fill} name={bar.name} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

// ============================================
// PIE CHART
// ============================================
interface PieChartData {
  name: string;
  value: number;
}

interface CustomPieChartProps {
  data: PieChartData[];
  title?: string;
  description?: string;
  height?: number;
  colors?: string[];
}

const DEFAULT_COLORS = ['#2563eb', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];

export function CustomPieChart({
  data,
  title,
  description,
  height = 300,
  colors = DEFAULT_COLORS,
}: CustomPieChartProps) {
  return (
    <Card>
      {(title || description) && (
        <CardHeader>
          {title && <CardTitle>{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <CardContent>
        <ResponsiveContainer width="100%" height={height}>
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              labelLine={false}
              label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
              outerRadius={80}
              fill="#8884d8"
              dataKey="value"
            >
              {data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={colors[index % colors.length]} />
              ))}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

// ============================================
// AREA CHART
// ============================================
interface AreaChartData {
  name: string;
  [key: string]: string | number;
}

interface CustomAreaChartProps {
  data: AreaChartData[];
  areas: { dataKey: string; fill: string; stroke: string; name: string }[];
  title?: string;
  description?: string;
  height?: number;
}

export function CustomAreaChart({
  data,
  areas,
  title,
  description,
  height = 300,
}: CustomAreaChartProps) {
  return (
    <Card>
      {(title || description) && (
        <CardHeader>
          {title && <CardTitle>{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
      )}
      <CardContent>
        <ResponsiveContainer width="100%" height={height}>
          <AreaChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Legend />
            {areas.map((area) => (
              <Area
                key={area.dataKey}
                type="monotone"
                dataKey={area.dataKey}
                stroke={area.stroke}
                fill={area.fill}
                name={area.name}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

// ============================================
// STAT CARD WITH TREND
// ============================================
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';

interface StatCardProps {
  title: string;
  value: string | number;
  change?: number;
  changeLabel?: string;
  icon?: React.ReactNode;
  description?: string;
}

export function StatCard({ title, value, change, changeLabel, icon, description }: StatCardProps) {
  const getTrendIcon = () => {
    if (change === undefined || change === 0) return <Minus className="h-4 w-4" />;
    return change > 0 ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />;
  };

  const getTrendColor = () => {
    if (change === undefined || change === 0) return 'text-muted-foreground';
    return change > 0 ? 'text-green-600' : 'text-red-600';
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {(change !== undefined || description) && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1">
            {change !== undefined && (
              <span className={`flex items-center gap-1 ${getTrendColor()}`}>
                {getTrendIcon()}
                {Math.abs(change)}%
              </span>
            )}
            {changeLabel && <span>{changeLabel}</span>}
            {description && <span>{description}</span>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
