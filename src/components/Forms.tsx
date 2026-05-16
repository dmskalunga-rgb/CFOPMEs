import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { CalendarIcon, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { format } from "date-fns";
import { pt } from "date-fns/locale";
import { cn } from "@/lib/utils";
import {
  Invoice,
  InvoiceItem,
  Employee,
  TransactionType,
  TRANSACTION_CATEGORIES,
  DEPARTMENTS,
  POSITIONS,
} from "@/lib/index";
import { calculateIVA } from "@/lib/tax-calculator";

const loginSchema = z.object({
  email: z.string().email("Email inválido"),
  password: z.string().min(6, "Senha deve ter no mínimo 6 caracteres"),
  rememberMe: z.boolean().optional(),
});

type LoginFormData = z.infer<typeof loginSchema>;

interface LoginFormProps {
  onSubmit: (data: LoginFormData) => void;
}

export function LoginForm({ onSubmit }: LoginFormProps) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
    defaultValues: {
      rememberMe: false,
    },
  });

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
      <div className="space-y-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          placeholder="seu@email.com"
          {...register("email")}
          className={cn(errors.email && "border-destructive")}
        />
        {errors.email && (
          <p className="text-sm text-destructive">{errors.email.message}</p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="password">Senha</Label>
        <Input
          id="password"
          type="password"
          placeholder="••••••••"
          {...register("password")}
          className={cn(errors.password && "border-destructive")}
        />
        {errors.password && (
          <p className="text-sm text-destructive">{errors.password.message}</p>
        )}
      </div>

      <div className="flex items-center space-x-2">
        <Checkbox id="rememberMe" {...register("rememberMe")} />
        <Label htmlFor="rememberMe" className="text-sm font-normal cursor-pointer">
          Lembrar-me
        </Label>
      </div>

      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Entrando..." : "Entrar"}
      </Button>
    </form>
  );
}

const invoiceItemSchema = z.object({
  description: z.string().min(1, "Descrição obrigatória"),
  quantity: z.number().min(0.01, "Quantidade deve ser maior que 0"),
  unitPrice: z.number().min(0, "Preço unitário inválido"),
  taxRate: z.number().min(0).max(100, "Taxa de imposto inválida"),
});

const invoiceSchema = z.object({
  clientName: z.string().min(1, "Nome do cliente obrigatório"),
  clientNif: z.string().min(9, "NIF inválido").max(9, "NIF inválido"),
  clientAddress: z.string().min(1, "Endereço obrigatório"),
  date: z.date(),
  dueDate: z.date(),
  items: z.array(invoiceItemSchema).min(1, "Adicione pelo menos um item"),
  notes: z.string().optional(),
});

type InvoiceFormData = z.infer<typeof invoiceSchema>;

interface InvoiceFormProps {
  onSubmit: (data: InvoiceFormData) => void;
  initialData?: Invoice;
}

export function InvoiceForm({ onSubmit, initialData }: InvoiceFormProps) {
  const [items, setItems] = useState<InvoiceItem[]>(
    initialData?.items || [
      { id: "1", description: "", quantity: 1, unitPrice: 0, taxRate: 14, total: 0 },
    ]
  );

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setValue,
    watch,
  } = useForm<InvoiceFormData>({
    resolver: zodResolver(invoiceSchema),
    defaultValues: {
      clientName: initialData?.clientName || "",
      clientNif: initialData?.clientNif || "",
      clientAddress: initialData?.clientAddress || "",
      date: initialData?.date || new Date(),
      dueDate: initialData?.dueDate || new Date(),
      items: items,
      notes: initialData?.notes || "",
    },
  });

  const date = watch("date");
  const dueDate = watch("dueDate");

  const addItem = () => {
    const newItem: InvoiceItem = {
      id: Date.now().toString(),
      description: "",
      quantity: 1,
      unitPrice: 0,
      taxRate: 14,
      total: 0,
    };
    const updatedItems = [...items, newItem];
    setItems(updatedItems);
    setValue("items", updatedItems);
  };

  const removeItem = (id: string) => {
    const updatedItems = items.filter((item) => item.id !== id);
    setItems(updatedItems);
    setValue("items", updatedItems);
  };

  const updateItem = (id: string, field: keyof InvoiceItem, value: any) => {
    const updatedItems = items.map((item) => {
      if (item.id === id) {
        const updated = { ...item, [field]: value };
        const subtotal = updated.quantity * updated.unitPrice;
        const ivaCalc = calculateIVA(subtotal, updated.taxRate === 14 ? "normal" : updated.taxRate === 5 ? "reduced" : "exempt");
        updated.total = ivaCalc.totalAmount;
        return updated;
      }
      return item;
    });
    setItems(updatedItems);
    setValue("items", updatedItems);
  };

  const subtotal = items.reduce((sum, item) => sum + item.quantity * item.unitPrice, 0);
  const taxAmount = items.reduce((sum, item) => {
    const itemSubtotal = item.quantity * item.unitPrice;
    const ivaCalc = calculateIVA(itemSubtotal, item.taxRate === 14 ? "normal" : item.taxRate === 5 ? "reduced" : "exempt");
    return sum + ivaCalc.ivaAmount;
  }, 0);
  const total = subtotal + taxAmount;

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="clientName">Nome do Cliente</Label>
          <Input
            id="clientName"
            placeholder="Nome completo"
            {...register("clientName")}
            className={cn(errors.clientName && "border-destructive")}
          />
          {errors.clientName && (
            <p className="text-sm text-destructive">{errors.clientName.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="clientNif">NIF do Cliente</Label>
          <Input
            id="clientNif"
            placeholder="000000000"
            maxLength={9}
            {...register("clientNif")}
            className={cn(errors.clientNif && "border-destructive")}
          />
          {errors.clientNif && (
            <p className="text-sm text-destructive">{errors.clientNif.message}</p>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="clientAddress">Endereço do Cliente</Label>
        <Input
          id="clientAddress"
          placeholder="Rua, Bairro, Cidade"
          {...register("clientAddress")}
          className={cn(errors.clientAddress && "border-destructive")}
        />
        {errors.clientAddress && (
          <p className="text-sm text-destructive">{errors.clientAddress.message}</p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Data de Emissão</Label>
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className={cn(
                  "w-full justify-start text-left font-normal",
                  !date && "text-muted-foreground"
                )}
              >
                <CalendarIcon className="mr-2 h-4 w-4" />
                {date ? format(date, "PPP", { locale: pt }) : "Selecione a data"}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0">
              <Calendar
                mode="single"
                selected={date}
                onSelect={(newDate) => newDate && setValue("date", newDate)}
                initialFocus
              />
            </PopoverContent>
          </Popover>
        </div>

        <div className="space-y-2">
          <Label>Data de Vencimento</Label>
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className={cn(
                  "w-full justify-start text-left font-normal",
                  !dueDate && "text-muted-foreground"
                )}
              >
                <CalendarIcon className="mr-2 h-4 w-4" />
                {dueDate ? format(dueDate, "PPP", { locale: pt }) : "Selecione a data"}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0">
              <Calendar
                mode="single"
                selected={dueDate}
                onSelect={(newDate) => newDate && setValue("dueDate", newDate)}
                initialFocus
              />
            </PopoverContent>
          </Popover>
        </div>
      </div>

      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <Label className="text-lg font-semibold">Itens da Fatura</Label>
          <Button type="button" onClick={addItem} size="sm" variant="outline">
            <Plus className="h-4 w-4 mr-2" />
            Adicionar Item
          </Button>
        </div>

        <div className="space-y-3">
          {items.map((item, index) => (
            <div key={item.id} className="p-4 border border-border rounded-lg space-y-3">
              <div className="flex items-start justify-between">
                <span className="text-sm font-medium text-muted-foreground">Item {index + 1}</span>
                {items.length > 1 && (
                  <Button
                    type="button"
                    onClick={() => removeItem(item.id)}
                    size="sm"
                    variant="ghost"
                    className="h-8 w-8 p-0 text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                <div className="md:col-span-2 space-y-2">
                  <Label>Descrição</Label>
                  <Input
                    placeholder="Descrição do item"
                    value={item.description}
                    onChange={(e) => updateItem(item.id, "description", e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label>Quantidade</Label>
                  <Input
                    type="number"
                    step="0.01"
                    min="0.01"
                    value={item.quantity}
                    onChange={(e) => updateItem(item.id, "quantity", parseFloat(e.target.value) || 0)}
                  />
                </div>

                <div className="space-y-2">
                  <Label>Preço Unitário</Label>
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    value={item.unitPrice}
                    onChange={(e) => updateItem(item.id, "unitPrice", parseFloat(e.target.value) || 0)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="space-y-2">
                  <Label>Taxa IVA (%)</Label>
                  <Select
                    value={item.taxRate.toString()}
                    onValueChange={(value) => updateItem(item.id, "taxRate", parseFloat(value))}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="0">Isento (0%)</SelectItem>
                      <SelectItem value="5">Reduzida (5%)</SelectItem>
                      <SelectItem value="14">Normal (14%)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label>Subtotal</Label>
                  <Input
                    value={(item.quantity * item.unitPrice).toFixed(2)}
                    disabled
                    className="bg-muted"
                  />
                </div>

                <div className="space-y-2">
                  <Label>Total (c/ IVA)</Label>
                  <Input
                    value={item.total.toFixed(2)}
                    disabled
                    className="bg-muted font-semibold"
                  />
                </div>
              </div>
            </div>
          ))}
        </div>

        {errors.items && (
          <p className="text-sm text-destructive">{errors.items.message}</p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="notes">Notas (Opcional)</Label>
        <Textarea
          id="notes"
          placeholder="Observações adicionais"
          rows={3}
          {...register("notes")}
        />
      </div>

      <div className="border-t border-border pt-4 space-y-2">
        <div className="flex justify-between text-sm">
          <span className="text-muted-foreground">Subtotal:</span>
          <span className="font-medium">{subtotal.toFixed(2)} Kz</span>
        </div>
        <div className="flex justify-between text-sm">
          <span className="text-muted-foreground">IVA:</span>
          <span className="font-medium">{taxAmount.toFixed(2)} Kz</span>
        </div>
        <div className="flex justify-between text-lg font-bold">
          <span>Total:</span>
          <span className="text-primary">{total.toFixed(2)} Kz</span>
        </div>
      </div>

      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Salvando..." : initialData ? "Atualizar Fatura" : "Criar Fatura"}
      </Button>
    </form>
  );
}

const transactionSchema = z.object({
  type: z.nativeEnum(TransactionType),
  category: z.string().min(1, "Categoria obrigatória"),
  description: z.string().min(1, "Descrição obrigatória"),
  amount: z.number().min(0.01, "Valor deve ser maior que 0"),
  date: z.date(),
  paymentMethod: z.string().min(1, "Método de pagamento obrigatório"),
  account: z.string().min(1, "Conta obrigatória"),
  reference: z.string().optional(),
});

type TransactionFormData = z.infer<typeof transactionSchema>;

interface TransactionFormProps {
  onSubmit: (data: TransactionFormData) => void;
  onChange?: (data: Partial<TransactionFormData>) => void;
}

export function TransactionForm({ onSubmit }: TransactionFormProps) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setValue,
    watch,
  } = useForm<TransactionFormData>({
    resolver: zodResolver(transactionSchema),
    defaultValues: {
      type: TransactionType.INCOME,
      date: new Date(),
    },
  });

  const type = watch("type");
  const date = watch("date");
  const category = watch("category");

  const categories = type === TransactionType.INCOME ? TRANSACTION_CATEGORIES.INCOME : TRANSACTION_CATEGORIES.EXPENSE;

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Tipo de Transação</Label>
        <Select
          value={type}
          onValueChange={(value) => {
            setValue("type", value as TransactionType);
            setValue("category", "");
          }}
        >
          <SelectTrigger className={cn(errors.type && "border-destructive")}>
            <SelectValue placeholder="Selecione o tipo" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={TransactionType.INCOME}>Receita</SelectItem>
            <SelectItem value={TransactionType.EXPENSE}>Despesa</SelectItem>
          </SelectContent>
        </Select>
          {errors.type && (
            <p className="text-sm text-destructive">{errors.type.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label>Categoria</Label>
        <Select value={category} onValueChange={(value) => setValue("category", value)}>
          <SelectTrigger className={cn(errors.category && "border-destructive")}>
            <SelectValue placeholder="Selecione a categoria" />
          </SelectTrigger>
          <SelectContent>
            {categories.map((cat) => (
              <SelectItem key={cat} value={cat}>
                {cat}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
          {errors.category && (
            <p className="text-sm text-destructive">{errors.category.message}</p>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="description">Descrição</Label>
        <Input
          id="description"
          placeholder="Descrição da transação"
          {...register("description")}
          className={cn(errors.description && "border-destructive")}
        />
        {errors.description && (
          <p className="text-sm text-destructive">{errors.description.message}</p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="amount">Valor (Kz)</Label>
          <Input
            id="amount"
            type="number"
            step="0.01"
            min="0.01"
            placeholder="0.00"
            {...register("amount", { valueAsNumber: true })}
            className={cn(errors.amount && "border-destructive")}
          />
          {errors.amount && (
            <p className="text-sm text-destructive">{errors.amount.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label>Data</Label>
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className={cn(
                  "w-full justify-start text-left font-normal",
                  !date && "text-muted-foreground",
                  errors.date && "border-destructive"
                )}
              >
                <CalendarIcon className="mr-2 h-4 w-4" />
                {date ? format(date, "PPP", { locale: pt }) : "Selecione a data"}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0">
              <Calendar
                mode="single"
                selected={date}
                onSelect={(newDate) => newDate && setValue("date", newDate)}
                initialFocus
              />
            </PopoverContent>
          </Popover>
          {errors.date && (
            <p className="text-sm text-destructive">{errors.date.message}</p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Método de Pagamento</Label>
          <Select
            value={watch("paymentMethod")}
            onValueChange={(value) => setValue("paymentMethod", value)}
          >
            <SelectTrigger className={cn(errors.paymentMethod && "border-destructive")}>
              <SelectValue placeholder="Selecione o método" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="Dinheiro">Dinheiro</SelectItem>
              <SelectItem value="Transferência Bancária">Transferência Bancária</SelectItem>
              <SelectItem value="Cartão de Crédito">Cartão de Crédito</SelectItem>
              <SelectItem value="Cartão de Débito">Cartão de Débito</SelectItem>
              <SelectItem value="Cheque">Cheque</SelectItem>
              <SelectItem value="Multicaixa">Multicaixa</SelectItem>
              <SelectItem value="Outro">Outro</SelectItem>
            </SelectContent>
          </Select>
          {errors.paymentMethod && (
            <p className="text-sm text-destructive">{errors.paymentMethod.message}</p>
          )}
        </div>

        <div className="space-y-2">
          <Label>Conta/Banco</Label>
          <Select
            value={watch("account")}
            onValueChange={(value) => setValue("account", value)}
          >
            <SelectTrigger className={cn(errors.account && "border-destructive")}>
              <SelectValue placeholder="Selecione a conta" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="Caixa">Caixa</SelectItem>
              <SelectItem value="BAI - Conta Corrente">BAI - Conta Corrente</SelectItem>
              <SelectItem value="BFA - Conta Corrente">BFA - Conta Corrente</SelectItem>
              <SelectItem value="Millennium - Conta Corrente">Millennium - Conta Corrente</SelectItem>
              <SelectItem value="BPC - Conta Corrente">BPC - Conta Corrente</SelectItem>
              <SelectItem value="Outro">Outro</SelectItem>
            </SelectContent>
          </Select>
          {errors.account && (
            <p className="text-sm text-destructive">{errors.account.message}</p>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="reference">Referência (Opcional)</Label>
        <Input
          id="reference"
          placeholder="Número de referência ou documento"
          {...register("reference")}
        />
      </div>

      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Salvando..." : "Criar Transação"}
      </Button>
    </form>
  );
}

const employeeSchema = z.object({
  name: z.string().min(1, "Nome obrigatório"),
  nif: z.string().min(9, "NIF inválido").max(9, "NIF inválido"),
  email: z.string().email("Email inválido"),
  phone: z.string().min(9, "Telefone inválido"),
  position: z.string().min(1, "Cargo obrigatório"),
  department: z.string().min(1, "Departamento obrigatório"),
  baseSalary: z.number().min(0, "Salário base inválido"),
  startDate: z.date(),
});

type EmployeeFormData = z.infer<typeof employeeSchema>;

interface EmployeeFormProps {
  onSubmit: (data: EmployeeFormData) => void;
  initialData?: Employee;
}

export function EmployeeForm({ onSubmit, initialData }: EmployeeFormProps) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setValue,
    watch,
  } = useForm<EmployeeFormData>({
    resolver: zodResolver(employeeSchema),
    defaultValues: {
      name: initialData?.name || "",
      nif: initialData?.nif || "",
      email: initialData?.email || "",
      phone: initialData?.phone || "",
      position: initialData?.position || "",
      department: initialData?.department || "",
      baseSalary: initialData?.baseSalary || 0,
      startDate: initialData?.startDate || new Date(),
    },
  });

  const startDate = watch("startDate");
  const position = watch("position");
  const department = watch("department");

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
      <div className="space-y-4">
        <h3 className="text-lg font-semibold">Dados Pessoais</h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="name">Nome Completo</Label>
            <Input
              id="name"
              placeholder="Nome completo do funcionário"
              {...register("name")}
              className={cn(errors.name && "border-destructive")}
            />
            {errors.name && (
              <p className="text-sm text-destructive">{errors.name.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="nif">NIF</Label>
            <Input
              id="nif"
              placeholder="000000000"
              maxLength={9}
              {...register("nif")}
              className={cn(errors.nif && "border-destructive")}
            />
            {errors.nif && (
              <p className="text-sm text-destructive">{errors.nif.message}</p>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              placeholder="email@exemplo.com"
              {...register("email")}
              className={cn(errors.email && "border-destructive")}
            />
            {errors.email && (
              <p className="text-sm text-destructive">{errors.email.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="phone">Telefone</Label>
            <Input
              id="phone"
              placeholder="900000000"
              {...register("phone")}
              className={cn(errors.phone && "border-destructive")}
            />
            {errors.phone && (
              <p className="text-sm text-destructive">{errors.phone.message}</p>
            )}
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <h3 className="text-lg font-semibold">Informações Profissionais</h3>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>Cargo</Label>
            <Select value={position} onValueChange={(value) => setValue("position", value)}>
              <SelectTrigger className={cn(errors.position && "border-destructive")}>
                <SelectValue placeholder="Selecione o cargo" />
              </SelectTrigger>
              <SelectContent>
                {POSITIONS.map((pos) => (
                  <SelectItem key={pos} value={pos}>
                    {pos}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errors.position && (
              <p className="text-sm text-destructive">{errors.position.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label>Departamento</Label>
            <Select value={department} onValueChange={(value) => setValue("department", value)}>
              <SelectTrigger className={cn(errors.department && "border-destructive")}>
                <SelectValue placeholder="Selecione o departamento" />
              </SelectTrigger>
              <SelectContent>
                {DEPARTMENTS.map((dept) => (
                  <SelectItem key={dept} value={dept}>
                    {dept}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errors.department && (
              <p className="text-sm text-destructive">{errors.department.message}</p>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="baseSalary">Salário Base (Kz)</Label>
            <Input
              id="baseSalary"
              type="number"
              step="0.01"
              min="0"
              placeholder="0.00"
              {...register("baseSalary", { valueAsNumber: true })}
              className={cn(errors.baseSalary && "border-destructive")}
            />
            {errors.baseSalary && (
              <p className="text-sm text-destructive">{errors.baseSalary.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label>Data de Início</Label>
            <Popover>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  className={cn(
                    "w-full justify-start text-left font-normal",
                    !startDate && "text-muted-foreground",
                    errors.startDate && "border-destructive"
                  )}
                >
                  <CalendarIcon className="mr-2 h-4 w-4" />
                  {startDate ? format(startDate, "PPP", { locale: pt }) : "Selecione a data"}
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-auto p-0">
                <Calendar
                  mode="single"
                  selected={startDate}
                  onSelect={(newDate) => newDate && setValue("startDate", newDate)}
                  initialFocus
                />
              </PopoverContent>
            </Popover>
            {errors.startDate && (
              <p className="text-sm text-destructive">{errors.startDate.message}</p>
            )}
          </div>
        </div>
      </div>

      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Salvando..." : initialData ? "Atualizar Funcionário" : "Adicionar Funcionário"}
      </Button>
    </form>
  );
}
