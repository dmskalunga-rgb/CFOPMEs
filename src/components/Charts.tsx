import { formatCurrency } from "@/lib/index";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface CashFlowData {
  month: string;
  receitas: number;
  despesas: number;
}

export interface RevenueData {
  month: string;
  valor: number;
}

export interface CategoryData {
  categoria: string;
  valor: number;
}

export interface PayrollData {
  month: string;
  valor: number;
}

interface CashFlowChartProps {
  data: CashFlowData[];
}

interface RevenueChartProps {
  data: RevenueData[];
}

interface ExpensesByCategoryChartProps {
  data: CategoryData[];
}

interface PayrollTrendChartProps {
  data: PayrollData[];
}

const CHART_COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

const CustomTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="rounded-lg border border-border bg-card p-3 shadow-lg">
        <p className="mb-2 font-medium text-card-foreground">{label}</p>
        {payload.map((entry: any, index: number) => (
          <p key={index} className="text-sm" style={{ color: entry.color }}>
            {entry.name}: {formatCurrency(entry.value)}
          </p>
        ))}
      </div>
    );
  }
  return null;
};

export function CashFlowChart({ data }: CashFlowChartProps) {
  return (
    <ResponsiveContainer width="100%" height={300}>
      <AreaChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="colorReceitas" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={CHART_COLORS[1]} stopOpacity={0.3} />
            <stop offset="95%" stopColor={CHART_COLORS[1]} stopOpacity={0} />
          </linearGradient>
          <linearGradient id="colorDespesas" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={CHART_COLORS[0]} stopOpacity={0.3} />
            <stop offset="95%" stopColor={CHART_COLORS[0]} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
        <XAxis
          dataKey="month"
          stroke="hsl(var(--muted-foreground))"
          fontSize={12}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          stroke="hsl(var(--muted-foreground))"
          fontSize={12}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${(value / 1000).toFixed(0)}K`}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend
          wrapperStyle={{
            paddingTop: "20px",
            fontSize: "14px",
            fontWeight: 500,
          }}
        />
        <Area
          type="monotone"
          dataKey="receitas"
          name="Receitas"
          stroke={CHART_COLORS[1]}
          strokeWidth={2}
          fillOpacity={1}
          fill="url(#colorReceitas)"
        />
        <Area
          type="monotone"
          dataKey="despesas"
          name="Despesas"
          stroke={CHART_COLORS[0]}
          strokeWidth={2}
          fillOpacity={1}
          fill="url(#colorDespesas)"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function RevenueChart({ data }: RevenueChartProps) {
  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
        <XAxis
          dataKey="month"
          stroke="hsl(var(--muted-foreground))"
          fontSize={12}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          stroke="hsl(var(--muted-foreground))"
          fontSize={12}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${(value / 1000).toFixed(0)}K`}
        />
        <Tooltip content={<CustomTooltip />} />
        <Bar
          dataKey="valor"
          name="Receitas"
          fill={CHART_COLORS[1]}
          radius={[8, 8, 0, 0]}
          maxBarSize={60}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function ExpensesByCategoryChart({ data }: ExpensesByCategoryChartProps) {
  const total = data.reduce((sum, item) => sum + item.valor, 0);

  const CustomPieTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const percentage = ((payload[0].value / total) * 100).toFixed(1);
      return (
        <div className="rounded-lg border border-border bg-card p-3 shadow-lg">
          <p className="mb-1 font-medium text-card-foreground">
            {payload[0].name}
          </p>
          <p className="text-sm text-muted-foreground">
            {formatCurrency(payload[0].value)} ({percentage}%)
          </p>
        </div>
      );
    }
    return null;
  };

  const CustomLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percent }: any) => {
    const RADIAN = Math.PI / 180;
    const radius = innerRadius + (outerRadius - innerRadius) * 0.5;
    const x = cx + radius * Math.cos(-midAngle * RADIAN);
    const y = cy + radius * Math.sin(-midAngle * RADIAN);

    if (percent < 0.05) return null;

    return (
      <text
        x={x}
        y={y}
        fill="white"
        textAnchor={x > cx ? "start" : "end"}
        dominantBaseline="central"
        className="text-xs font-semibold"
      >
        {`${(percent * 100).toFixed(0)}%`}
      </text>
    );
  };

  return (
    <ResponsiveContainer width="100%" height={300}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          labelLine={false}
          label={CustomLabel}
          outerRadius={100}
          innerRadius={60}
          fill="#8884d8"
          dataKey="valor"
          nameKey="categoria"
        >
          {data.map((_, index) => (
            <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
          ))}
        </Pie>
        <Tooltip content={<CustomPieTooltip />} />
        <Legend
          verticalAlign="bottom"
          height={36}
          wrapperStyle={{
            paddingTop: "20px",
            fontSize: "12px",
          }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}

export function PayrollTrendChart({ data }: PayrollTrendChartProps) {
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
        <XAxis
          dataKey="month"
          stroke="hsl(var(--muted-foreground))"
          fontSize={12}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          stroke="hsl(var(--muted-foreground))"
          fontSize={12}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${(value / 1000).toFixed(0)}K`}
        />
        <Tooltip content={<CustomTooltip />} />
        <Legend
          wrapperStyle={{
            paddingTop: "20px",
            fontSize: "14px",
            fontWeight: 500,
          }}
        />
        <Line
          type="monotone"
          dataKey="valor"
          name="Folha Salarial"
          stroke={CHART_COLORS[3]}
          strokeWidth={3}
          dot={{
            fill: CHART_COLORS[3],
            strokeWidth: 2,
            r: 5,
          }}
          activeDot={{
            r: 7,
            strokeWidth: 2,
          }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}