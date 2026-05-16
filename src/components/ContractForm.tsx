import { useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Contract } from '@/services/contractManagementService';
import { useToast } from '@/lib/toast-provider';

interface ContractFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: any) => Promise<void>;
  contract?: Contract | null;
}

export function ContractForm({ open, onOpenChange, onSubmit, contract }: ContractFormProps) {
  const { success, error: showError } = useToast();
  const [loading, setLoading] = useState(false);

  const [formData, setFormData] = useState({
    type: contract?.type || 'supplier',
    name: contract?.name || '',
    counterparty: contract?.counterparty || '',
    description: contract?.description || '',
    value: contract?.value || 0,
    currency: contract?.currency || 'AOA',
    startDate: contract?.startDate?.toISOString().slice(0, 10) || new Date().toISOString().slice(0, 10),
    endDate: contract?.endDate?.toISOString().slice(0, 10) || new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
    renewalType: contract?.renewalType || 'manual',
    renewalNoticeDays: contract?.renewalNoticeDays || 30,
    paymentFrequency: contract?.paymentFrequency || 'monthly',
    nextPaymentDate: contract?.nextPaymentDate?.toISOString().slice(0, 10) || '',
    status: contract?.status || 'draft',
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!formData.name || !formData.counterparty || !formData.value) {
      showError('Erro', 'Preencha todos os campos obrigatórios');
      return;
    }

    setLoading(true);
    try {
      await onSubmit({
        ...formData,
        startDate: new Date(formData.startDate),
        endDate: new Date(formData.endDate),
        nextPaymentDate: formData.nextPaymentDate ? new Date(formData.nextPaymentDate) : undefined,
      });
      success('Sucesso', contract ? 'Contrato atualizado' : 'Contrato criado');
      onOpenChange(false);
    } catch (err) {
      showError('Erro', 'Não foi possível salvar o contrato');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{contract ? 'Editar Contrato' : 'Novo Contrato'}</DialogTitle>
          <DialogDescription>
            Preencha os dados do contrato
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Tipo de Contrato *</Label>
              <Select value={formData.type} onValueChange={(value) => setFormData({ ...formData, type: value as any })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="supplier">Fornecedor</SelectItem>
                  <SelectItem value="customer">Cliente</SelectItem>
                  <SelectItem value="employee">Funcionário</SelectItem>
                  <SelectItem value="service">Serviço</SelectItem>
                  <SelectItem value="lease">Arrendamento</SelectItem>
                  <SelectItem value="other">Outro</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Status</Label>
              <Select value={formData.status} onValueChange={(value) => setFormData({ ...formData, status: value as any })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="draft">Rascunho</SelectItem>
                  <SelectItem value="active">Ativo</SelectItem>
                  <SelectItem value="expired">Expirado</SelectItem>
                  <SelectItem value="cancelled">Cancelado</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label>Nome do Contrato *</Label>
            <Input
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="Ex: Fornecimento de Material"
            />
          </div>

          <div className="space-y-2">
            <Label>Contraparte *</Label>
            <Input
              value={formData.counterparty}
              onChange={(e) => setFormData({ ...formData, counterparty: e.target.value })}
              placeholder="Nome da empresa/pessoa"
            />
          </div>

          <div className="space-y-2">
            <Label>Descrição</Label>
            <Textarea
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              placeholder="Detalhes do contrato"
              rows={3}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Valor *</Label>
              <Input
                type="number"
                step="0.01"
                value={formData.value}
                onChange={(e) => setFormData({ ...formData, value: parseFloat(e.target.value) || 0 })}
                placeholder="0.00"
              />
            </div>

            <div className="space-y-2">
              <Label>Moeda</Label>
              <Select value={formData.currency} onValueChange={(value) => setFormData({ ...formData, currency: value })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="AOA">AOA (Kwanza)</SelectItem>
                  <SelectItem value="USD">USD (Dólar)</SelectItem>
                  <SelectItem value="EUR">EUR (Euro)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Data Início *</Label>
              <Input
                type="date"
                value={formData.startDate}
                onChange={(e) => setFormData({ ...formData, startDate: e.target.value })}
              />
            </div>

            <div className="space-y-2">
              <Label>Data Fim *</Label>
              <Input
                type="date"
                value={formData.endDate}
                onChange={(e) => setFormData({ ...formData, endDate: e.target.value })}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Tipo de Renovação</Label>
              <Select value={formData.renewalType} onValueChange={(value) => setFormData({ ...formData, renewalType: value as any })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="manual">Manual</SelectItem>
                  <SelectItem value="automatic">Automática</SelectItem>
                  <SelectItem value="none">Sem Renovação</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Dias de Aviso</Label>
              <Input
                type="number"
                value={formData.renewalNoticeDays}
                onChange={(e) => setFormData({ ...formData, renewalNoticeDays: parseInt(e.target.value) || 30 })}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Frequência de Pagamento</Label>
              <Select value={formData.paymentFrequency} onValueChange={(value) => setFormData({ ...formData, paymentFrequency: value as any })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="monthly">Mensal</SelectItem>
                  <SelectItem value="quarterly">Trimestral</SelectItem>
                  <SelectItem value="yearly">Anual</SelectItem>
                  <SelectItem value="one_time">Pagamento Único</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Próximo Pagamento</Label>
              <Input
                type="date"
                value={formData.nextPaymentDate}
                onChange={(e) => setFormData({ ...formData, nextPaymentDate: e.target.value })}
              />
            </div>
          </div>

          <div className="flex gap-2 justify-end pt-4">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? 'Salvando...' : contract ? 'Atualizar' : 'Criar'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
