import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { TrendingUp, TrendingDown, Edit, Trash2, Download, Eye, AlertCircle, CheckCircle, XCircle, Info } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Invoice, Transaction, Employee, Alert as AlertType, InvoiceStatus, TransactionType, formatCurrency, formatDate } from "@/lib/index";
import { LineChart, Line, ResponsiveContainer } from "recharts";

interface StatCardProps {
  title: string;
  value: string;
  change: number;
  icon: LucideIcon;
  trend: number[];
}

export function StatCard({ title, value, change, icon: Icon, trend }: StatCardProps) {
  const isPositive = change >= 0;
  const trendData = trend.map((value, index) => ({ index, value }));

  return (
    <Card className="relative overflow-hidden transition-all duration-200 hover:shadow-lg">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center">
          <Icon className="h-5 w-5 text-primary" />
        </div>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-bold tracking-tight">{value}</div>
        <div className="flex items-center justify-between mt-4">
          <div className="flex items-center gap-1 text-sm">
            {isPositive ? (
              <TrendingUp className="h-4 w-4 text-emerald-600" />
            ) : (
              <TrendingDown className="h-4 w-4 text-destructive" />
            )}
            <span className={isPositive ? "text-emerald-600 font-medium" : "text-destructive font-medium"}>
              {isPositive ? "+" : ""}{change.toFixed(1)}%
            </span>
            <span className="text-muted-foreground ml-1">vs mês anterior</span>
          </div>
          <div className="h-8 w-20">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendData}>
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke={isPositive ? "rgb(5, 150, 105)" : "rgb(220, 38, 38)"}
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

interface InvoiceCardProps {
  invoice: Invoice;
}

export function InvoiceCard({ invoice }: InvoiceCardProps) {
  const getStatusBadge = (status: InvoiceStatus) => {
    const variants: Record<InvoiceStatus, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
      [InvoiceStatus.DRAFT]: { label: "Rascunho", variant: "secondary" },
      [InvoiceStatus.SENT_AGT]: { label: "Enviado AGT", variant: "default" },
      [InvoiceStatus.VALIDATED]: { label: "Validado", variant: "default" },
      [InvoiceStatus.REJECTED]: { label: "Rejeitado", variant: "destructive" },
      [InvoiceStatus.PAID]: { label: "Pago", variant: "default" },
      [InvoiceStatus.CANCELLED]: { label: "Cancelado", variant: "outline" },
    };
    const { label, variant } = variants[status];
    return <Badge variant={variant}>{label}</Badge>;
  };

  return (
    <Card className="transition-all duration-200 hover:shadow-md">
      <CardHeader>
        <div className="flex items-start justify-between">
          <div>
            <CardTitle className="text-lg">{invoice.number}</CardTitle>
            <CardDescription className="mt-1">{invoice.clientName}</CardDescription>
          </div>
          {getStatusBadge(invoice.status)}
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span className="text-muted-foreground">Data:</span>
            <span className="font-medium">{formatDate(invoice.date)}</span>
          </div>
          <div className="flex justify-between text-sm">
            <span className="text-muted-foreground">Vencimento:</span>
            <span className="font-medium">{formatDate(invoice.dueDate)}</span>
          </div>
          <div className="flex justify-between text-sm pt-2 border-t">
            <span className="text-muted-foreground">Total:</span>
            <span className="text-lg font-bold text-primary">{formatCurrency(invoice.total)}</span>
          </div>
        </div>
      </CardContent>
      <CardFooter className="flex gap-2">
        <Button variant="outline" size="sm" className="flex-1">
          <Eye className="h-4 w-4 mr-1" />
          Ver
        </Button>
        <Button variant="outline" size="sm" className="flex-1">
          <Edit className="h-4 w-4 mr-1" />
          Editar
        </Button>
        <Button variant="outline" size="sm">
          <Download className="h-4 w-4" />
        </Button>
      </CardFooter>
    </Card>
  );
}

interface TransactionCardProps {
  transaction: Transaction;
}

export function TransactionCard({ transaction }: TransactionCardProps) {
  const isIncome = transaction.type === TransactionType.INCOME;

  return (
    <Card className="transition-all duration-200 hover:shadow-md">
      <CardContent className="pt-6">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              <Badge variant="outline" className="text-xs">
                {transaction.category}
              </Badge>
            </div>
            <h4 className="font-medium text-sm mb-1">{transaction.description}</h4>
            <p className="text-xs text-muted-foreground">{formatDate(transaction.date)}</p>
          </div>
          <div className="text-right">
            <div className={`text-lg font-bold ${
              isIncome ? "text-emerald-600" : "text-destructive"
            }`}>
              {isIncome ? "+" : "-"}{formatCurrency(Math.abs(transaction.amount))}
            </div>
            <div className="text-xs text-muted-foreground mt-1">
              {isIncome ? "Receita" : "Despesa"}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

interface EmployeeCardProps {
  employee: Employee;
}

export function EmployeeCard({ employee }: EmployeeCardProps) {
  const initials = employee.name
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  return (
    <Card className="transition-all duration-200 hover:shadow-md">
      <CardContent className="pt-6">
        <div className="flex items-start gap-4">
          <Avatar className="h-12 w-12">
            <AvatarImage src={employee.avatar} alt={employee.name} />
            <AvatarFallback className="bg-primary/10 text-primary font-semibold">
              {initials}
            </AvatarFallback>
          </Avatar>
          <div className="flex-1 min-w-0">
            <h4 className="font-semibold text-sm truncate">{employee.name}</h4>
            <p className="text-xs text-muted-foreground truncate">{employee.position}</p>
            <p className="text-xs text-muted-foreground mt-1">{employee.department}</p>
            <div className="flex items-center justify-between mt-3">
              <span className="text-xs text-muted-foreground">Salário Base:</span>
              <span className="text-sm font-bold text-primary">{formatCurrency(employee.baseSalary)}</span>
            </div>
          </div>
        </div>
      </CardContent>
      <CardFooter className="flex gap-2 pt-0">
        <Button variant="outline" size="sm" className="flex-1">
          <Edit className="h-4 w-4 mr-1" />
          Editar
        </Button>
        <Button variant="outline" size="sm" className="flex-1">
          <Eye className="h-4 w-4 mr-1" />
          Ver
        </Button>
      </CardFooter>
    </Card>
  );
}

interface AlertCardProps {
  alert: AlertType;
}

export function AlertCard({ alert }: AlertCardProps) {
  const getAlertIcon = (type: AlertType["type"]) => {
    switch (type) {
      case "info":
        return <Info className="h-4 w-4" />;
      case "success":
        return <CheckCircle className="h-4 w-4" />;
      case "warning":
        return <AlertCircle className="h-4 w-4" />;
      case "error":
        return <XCircle className="h-4 w-4" />;
    }
  };

  const getAlertVariant = (type: AlertType["type"]): "default" | "destructive" => {
    return type === "error" ? "destructive" : "default";
  };

  return (
    <Alert variant={getAlertVariant(alert.type)} className="transition-all duration-200 hover:shadow-md">
      <div className="flex items-start gap-3">
        <div className="mt-0.5">{getAlertIcon(alert.type)}</div>
        <div className="flex-1 min-w-0">
          <AlertTitle className="text-sm font-semibold mb-1">{alert.title}</AlertTitle>
          <AlertDescription className="text-xs">{alert.message}</AlertDescription>
          <div className="text-xs text-muted-foreground mt-2">{formatDate(alert.date, "long")}</div>
        </div>
        {!alert.read && (
          <div className="h-2 w-2 rounded-full bg-primary flex-shrink-0 mt-1" />
        )}
      </div>
    </Alert>
  );
}