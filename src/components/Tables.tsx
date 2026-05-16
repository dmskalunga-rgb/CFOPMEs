import { useState } from 'react';
import {
  Invoice,
  Transaction,
  Employee,
  Payslip,
  InvoiceStatus,
  TransactionType,
  formatCurrency,
  formatDate,
  formatNIF,
} from '@/lib/index';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  ChevronLeft,
  ChevronRight,
  MoreVertical,
  Edit,
  Trash2,
  Download,
  Send,
  Eye,
  Printer,
} from 'lucide-react';

interface InvoicesTableProps {
  invoices: Invoice[];
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

export function InvoicesTable({ invoices, onEdit, onDelete }: InvoicesTableProps) {
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;
  const totalPages = Math.ceil(invoices.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const endIndex = startIndex + itemsPerPage;
  const currentInvoices = invoices.slice(startIndex, endIndex);

  const getStatusBadge = (status: InvoiceStatus) => {
    const statusConfig = {
      [InvoiceStatus.DRAFT]: { label: 'Rascunho', variant: 'secondary' as const },
      [InvoiceStatus.SENT_AGT]: { label: 'Enviado AGT', variant: 'default' as const },
      [InvoiceStatus.VALIDATED]: { label: 'Validado', variant: 'default' as const },
      [InvoiceStatus.REJECTED]: { label: 'Rejeitado', variant: 'destructive' as const },
      [InvoiceStatus.PAID]: { label: 'Pago', variant: 'default' as const },
      [InvoiceStatus.CANCELLED]: { label: 'Cancelado', variant: 'secondary' as const },
    };

    const config = statusConfig[status];
    return (
      <Badge variant={config.variant} className="font-medium">
        {config.label}
      </Badge>
    );
  };

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="font-semibold">Número</TableHead>
              <TableHead className="font-semibold">Cliente</TableHead>
              <TableHead className="font-semibold">Data</TableHead>
              <TableHead className="font-semibold">Vencimento</TableHead>
              <TableHead className="font-semibold text-right">Valor</TableHead>
              <TableHead className="font-semibold">Status</TableHead>
              <TableHead className="font-semibold text-right">Ações</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {currentInvoices.map((invoice, index) => (
              <TableRow
                key={invoice.id}
                className={`transition-colors hover:bg-muted/50 ${
                  index % 2 === 0 ? 'bg-background' : 'bg-muted/20'
                }`}
              >
                <TableCell className="font-mono font-medium">{invoice.number}</TableCell>
                <TableCell>
                  <div>
                    <div className="font-medium">{invoice.clientName}</div>
                    <div className="text-sm text-muted-foreground">
                      NIF: {formatNIF(invoice.clientNif)}
                    </div>
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatDate(invoice.date)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatDate(invoice.dueDate)}
                </TableCell>
                <TableCell className="text-right font-mono font-semibold">
                  {formatCurrency(invoice.total)}
                </TableCell>
                <TableCell>{getStatusBadge(invoice.status)}</TableCell>
                <TableCell className="text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8">
                        <MoreVertical className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => onEdit(invoice.id)}>
                        <Edit className="mr-2 h-4 w-4" />
                        Editar
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Eye className="mr-2 h-4 w-4" />
                        Visualizar
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Send className="mr-2 h-4 w-4" />
                        Enviar AGT
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Printer className="mr-2 h-4 w-4" />
                        Imprimir
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Download className="mr-2 h-4 w-4" />
                        Download PDF
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => onDelete(invoice.id)}
                        className="text-destructive focus:text-destructive"
                      >
                        <Trash2 className="mr-2 h-4 w-4" />
                        Eliminar
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            Mostrando {startIndex + 1} a {Math.min(endIndex, invoices.length)} de{' '}
            {invoices.length} faturas
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <div className="text-sm font-medium">
              Página {currentPage} de {totalPages}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

interface TransactionsTableProps {
  transactions: Transaction[];
}

export function TransactionsTable({ transactions }: TransactionsTableProps) {
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;
  const totalPages = Math.ceil(transactions.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const endIndex = startIndex + itemsPerPage;
  const currentTransactions = transactions.slice(startIndex, endIndex);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="font-semibold">Data</TableHead>
              <TableHead className="font-semibold">Tipo</TableHead>
              <TableHead className="font-semibold">Categoria</TableHead>
              <TableHead className="font-semibold">Descrição</TableHead>
              <TableHead className="font-semibold">Referência</TableHead>
              <TableHead className="font-semibold text-right">Valor</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {currentTransactions.map((transaction, index) => (
              <TableRow
                key={transaction.id}
                className={`transition-colors hover:bg-muted/50 ${
                  index % 2 === 0 ? 'bg-background' : 'bg-muted/20'
                }`}
              >
                <TableCell className="text-muted-foreground">
                  {formatDate(transaction.date)}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={transaction.type === TransactionType.INCOME ? 'default' : 'secondary'}
                  >
                    {transaction.type === TransactionType.INCOME ? 'Receita' : 'Despesa'}
                  </Badge>
                </TableCell>
                <TableCell className="font-medium">{transaction.category}</TableCell>
                <TableCell className="text-muted-foreground">
                  {transaction.description}
                </TableCell>
                <TableCell className="font-mono text-sm text-muted-foreground">
                  {transaction.reference || '—'}
                </TableCell>
                <TableCell
                  className={`text-right font-mono font-semibold ${
                    transaction.type === TransactionType.INCOME
                      ? 'text-emerald-600 dark:text-emerald-400'
                      : 'text-red-600 dark:text-red-400'
                  }`}
                >
                  {transaction.type === TransactionType.INCOME ? '+' : '-'}
                  {formatCurrency(transaction.amount)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            Mostrando {startIndex + 1} a {Math.min(endIndex, transactions.length)} de{' '}
            {transactions.length} transações
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <div className="text-sm font-medium">
              Página {currentPage} de {totalPages}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

interface EmployeesTableProps {
  employees: Employee[];
  onEdit: (id: string) => void;
}

export function EmployeesTable({ employees, onEdit }: EmployeesTableProps) {
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;
  const totalPages = Math.ceil(employees.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const endIndex = startIndex + itemsPerPage;
  const currentEmployees = employees.slice(startIndex, endIndex);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="font-semibold">Funcionário</TableHead>
              <TableHead className="font-semibold">NIF</TableHead>
              <TableHead className="font-semibold">Cargo</TableHead>
              <TableHead className="font-semibold">Departamento</TableHead>
              <TableHead className="font-semibold text-right">Salário Base</TableHead>
              <TableHead className="font-semibold">Status</TableHead>
              <TableHead className="font-semibold text-right">Ações</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {currentEmployees.map((employee, index) => (
              <TableRow
                key={employee.id}
                className={`transition-colors hover:bg-muted/50 ${
                  index % 2 === 0 ? 'bg-background' : 'bg-muted/20'
                }`}
              >
                <TableCell>
                  <div className="flex items-center gap-3">
                    {employee.avatar ? (
                      <img
                        src={employee.avatar}
                        alt={employee.name}
                        className="h-10 w-10 rounded-full object-cover"
                      />
                    ) : (
                      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary font-semibold">
                        {employee.name.charAt(0)}
                      </div>
                    )}
                    <div>
                      <div className="font-medium">{employee.name}</div>
                      <div className="text-sm text-muted-foreground">{employee.email}</div>
                    </div>
                  </div>
                </TableCell>
                <TableCell className="font-mono text-sm text-muted-foreground">
                  {formatNIF(employee.nif)}
                </TableCell>
                <TableCell className="font-medium">{employee.position}</TableCell>
                <TableCell className="text-muted-foreground">{employee.department}</TableCell>
                <TableCell className="text-right font-mono font-semibold">
                  {formatCurrency(employee.baseSalary)}
                </TableCell>
                <TableCell>
                  <Badge variant={employee.isActive ? 'default' : 'secondary'}>
                    {employee.isActive ? 'Ativo' : 'Inativo'}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8">
                        <MoreVertical className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => onEdit(employee.id)}>
                        <Edit className="mr-2 h-4 w-4" />
                        Editar
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Eye className="mr-2 h-4 w-4" />
                        Ver Detalhes
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Download className="mr-2 h-4 w-4" />
                        Exportar Dados
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            Mostrando {startIndex + 1} a {Math.min(endIndex, employees.length)} de{' '}
            {employees.length} funcionários
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <div className="text-sm font-medium">
              Página {currentPage} de {totalPages}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

interface PayslipsTableProps {
  payslips: Payslip[];
}

export function PayslipsTable({ payslips }: PayslipsTableProps) {
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;
  const totalPages = Math.ceil(payslips.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const endIndex = startIndex + itemsPerPage;
  const currentPayslips = payslips.slice(startIndex, endIndex);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="font-semibold">Funcionário</TableHead>
              <TableHead className="font-semibold">Período</TableHead>
              <TableHead className="font-semibold text-right">Salário Base</TableHead>
              <TableHead className="font-semibold text-right">INSS</TableHead>
              <TableHead className="font-semibold text-right">IRT</TableHead>
              <TableHead className="font-semibold text-right">Líquido</TableHead>
              <TableHead className="font-semibold">Status</TableHead>
              <TableHead className="font-semibold text-right">Ações</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {currentPayslips.map((payslip, index) => (
              <TableRow
                key={payslip.id}
                className={`transition-colors hover:bg-muted/50 ${
                  index % 2 === 0 ? 'bg-background' : 'bg-muted/20'
                }`}
              >
                <TableCell className="font-medium">{payslip.employeeName}</TableCell>
                <TableCell className="text-muted-foreground">
                  {payslip.month}/{payslip.year}
                </TableCell>
                <TableCell className="text-right font-mono">
                  {formatCurrency(payslip.baseSalary)}
                </TableCell>
                <TableCell className="text-right font-mono text-red-600 dark:text-red-400">
                  -{formatCurrency(payslip.inssEmployee)}
                </TableCell>
                <TableCell className="text-right font-mono text-red-600 dark:text-red-400">
                  -{formatCurrency(payslip.irt)}
                </TableCell>
                <TableCell className="text-right font-mono font-semibold text-emerald-600 dark:text-emerald-400">
                  {formatCurrency(payslip.netSalary)}
                </TableCell>
                <TableCell>
                  <Badge variant={payslip.paidAt ? 'default' : 'secondary'}>
                    {payslip.paidAt ? 'Pago' : 'Pendente'}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8">
                        <MoreVertical className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem>
                        <Eye className="mr-2 h-4 w-4" />
                        Visualizar
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Download className="mr-2 h-4 w-4" />
                        Download PDF
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Printer className="mr-2 h-4 w-4" />
                        Imprimir
                      </DropdownMenuItem>
                      <DropdownMenuItem>
                        <Send className="mr-2 h-4 w-4" />
                        Enviar por Email
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            Mostrando {startIndex + 1} a {Math.min(endIndex, payslips.length)} de{' '}
            {payslips.length} recibos
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <div className="text-sm font-medium">
              Página {currentPage} de {totalPages}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
