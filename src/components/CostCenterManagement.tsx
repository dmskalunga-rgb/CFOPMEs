import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { TrendingUp, Plus, Target, Edit } from 'lucide-react';
import { costCenterService, CostCenter } from '@/services/costCenterService';
import { CostCenterForm } from '@/components/CostCenterForm';
import { formatCurrency } from '@/lib/index';
import { useToast } from '@/hooks/use-toast';
import { CostCenterAnalysis } from '@/components/CostCenterAnalysis';

export function CostCenterManagement() {
  const { toast } = useToast();
  const [costCenters, setCostCenters] = useState<CostCenter[]>([]);
  const [loading, setLoading] = useState(true);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [selectedCostCenter, setSelectedCostCenter] = useState<CostCenter | null>(null);
  const [isAnalysisOpen, setIsAnalysisOpen] = useState(false);
  const [analyzingCostCenter, setAnalyzingCostCenter] = useState<CostCenter | null>(null);

  useEffect(() => {
    loadCostCenters();
  }, []);

  const loadCostCenters = async () => {
    setLoading(true);
    try {
      const data = await costCenterService.listCostCenters('comp-001', false);
      setCostCenters(data);
    } catch (err) {
      console.error('Erro ao carregar centros de custo:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = () => {
    setSelectedCostCenter(null);
    setIsFormOpen(true);
  };

  const handleEdit = (cc: CostCenter) => {
    setSelectedCostCenter(cc);
    setIsFormOpen(true);
  };

  const handleViewAnalysis = (cc: CostCenter) => {
    setAnalyzingCostCenter(cc);
    setIsAnalysisOpen(true);
  };

  const handleSubmit = async (data: any) => {
    try {
      if (selectedCostCenter) {
        // Update not implemented in service yet
        toast({
          title: 'Aviso',
          description: 'Atualização em desenvolvimento',
        });
      } else {
        await costCenterService.createCostCenter({
          companyId: 'comp-001',
          ...data,
        });
      }
      await loadCostCenters();
    } catch (err) {
      throw err;
    }
  };

  const getTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      department: 'Departamento',
      project: 'Projeto',
      product: 'Produto',
      location: 'Localização',
      custom: 'Customizado',
    };
    return labels[type] || type;
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Target className="h-12 w-12 animate-pulse text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <TrendingUp className="h-6 w-6 text-primary" />
            Centros de Custo
          </h2>
          <p className="text-muted-foreground mt-1">Análise de rentabilidade por centro de custo</p>
        </div>
        <Button className="gap-2" onClick={handleCreate}>
          <Plus className="h-4 w-4" />
          Novo Centro de Custo
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {costCenters.map((cc) => (
          <Card key={cc.id}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-lg">{cc.name}</CardTitle>
                <Badge variant={cc.active ? 'default' : 'secondary'}>
                  {cc.active ? 'Ativo' : 'Inativo'}
                </Badge>
              </div>
              <CardDescription>{cc.code}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">Tipo:</span>
                  <span className="font-medium">{getTypeLabel(cc.type)}</span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-muted-foreground">Orçamento:</span>
                  <span className="font-semibold">{formatCurrency(cc.budget)}</span>
                </div>
                <div className="flex gap-2 mt-4">
                  <Button variant="outline" size="sm" className="flex-1" onClick={() => handleEdit(cc)}>
                    <Edit className="h-4 w-4 mr-2" />
                    Editar
                  </Button>
                  <Button variant="outline" size="sm" className="flex-1" onClick={() => handleViewAnalysis(cc)}>
                    Ver Análise
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {costCenters.length === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <Target className="h-16 w-16 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold mb-2">Nenhum centro de custo criado</h3>
            <p className="text-sm text-muted-foreground mb-4">
              Crie centros de custo para analisar rentabilidade
            </p>
            <Button onClick={handleCreate}>
              <Plus className="h-4 w-4 mr-2" />
              Criar Primeiro Centro de Custo
            </Button>
          </CardContent>
        </Card>
      )}

      <CostCenterForm
        open={isFormOpen}
        onOpenChange={setIsFormOpen}
        onSubmit={handleSubmit}
        costCenter={selectedCostCenter}
      />

      <CostCenterAnalysis
        costCenter={analyzingCostCenter}
        open={isAnalysisOpen}
        onOpenChange={setIsAnalysisOpen}
      />
    </div>
  );
}
