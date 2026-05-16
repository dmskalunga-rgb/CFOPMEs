import { useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import { CostCenter } from '@/services/costCenterService';
import { useToast } from '@/lib/toast-provider';

interface CostCenterFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: any) => Promise<void>;
  costCenter?: CostCenter | null;
}

export function CostCenterForm({ open, onOpenChange, onSubmit, costCenter }: CostCenterFormProps) {
  const { success, error: showError } = useToast();
  const [loading, setLoading] = useState(false);

  const [formData, setFormData] = useState({
    code: costCenter?.code || '',
    name: costCenter?.name || '',
    description: costCenter?.description || '',
    type: costCenter?.type || 'department',
    budget: costCenter?.budget || 0,
    active: costCenter?.active ?? true,
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!formData.code || !formData.name) {
      showError('Erro', 'Preencha todos os campos obrigatórios');
      return;
    }

    setLoading(true);
    try {
      await onSubmit(formData);
      success('Sucesso', costCenter ? 'Centro de custo atualizado' : 'Centro de custo criado');
      onOpenChange(false);
    } catch (err) {
      showError('Erro', 'Não foi possível salvar o centro de custo');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{costCenter ? 'Editar Centro de Custo' : 'Novo Centro de Custo'}</DialogTitle>
          <DialogDescription>
            Preencha os dados do centro de custo
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Código *</Label>
              <Input
                value={formData.code}
                onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                placeholder="Ex: CC-001"
                disabled={!!costCenter}
              />
            </div>

            <div className="space-y-2">
              <Label>Tipo *</Label>
              <Select value={formData.type} onValueChange={(value) => setFormData({ ...formData, type: value as any })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="department">Departamento</SelectItem>
                  <SelectItem value="project">Projeto</SelectItem>
                  <SelectItem value="product">Produto</SelectItem>
                  <SelectItem value="location">Localização</SelectItem>
                  <SelectItem value="custom">Customizado</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label>Nome *</Label>
            <Input
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="Ex: Departamento Comercial"
            />
          </div>

          <div className="space-y-2">
            <Label>Descrição</Label>
            <Textarea
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              placeholder="Descrição do centro de custo"
              rows={3}
            />
          </div>

          <div className="space-y-2">
            <Label>Orçamento</Label>
            <Input
              type="number"
              step="0.01"
              value={formData.budget}
              onChange={(e) => setFormData({ ...formData, budget: parseFloat(e.target.value) || 0 })}
              placeholder="0.00"
            />
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              checked={formData.active}
              onCheckedChange={(checked) => setFormData({ ...formData, active: checked })}
            />
            <Label>Ativo</Label>
          </div>

          <div className="flex gap-2 justify-end pt-4">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? 'Salvando...' : costCenter ? 'Atualizar' : 'Criar'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
