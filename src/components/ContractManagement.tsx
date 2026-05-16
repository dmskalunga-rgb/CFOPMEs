import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Building2, Plus, AlertCircle, Calendar, DollarSign, Edit, Trash2 } from 'lucide-react';
import { contractManagementService, Contract, ContractSummary } from '@/services/contractManagementService';
import { ContractForm } from '@/components/ContractForm';
import { formatCurrency } from '@/lib/index';
import { useToast } from '@/lib/toast-provider';
import { motion } from 'framer-motion';

export function ContractManagement() {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [summary, setSummary] = useState<ContractSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [selectedContract, setSelectedContract] = useState<Contract | null>(null);
  const { success, error: showError } = useToast();

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [contractsData, summaryData] = await Promise.all([
        contractManagementService.listContracts('comp-001'),
        contractManagementService.getContractSummary('comp-001'),
      ]);
      setContracts(contractsData);
      setSummary(summaryData);
    } catch (err) {
      console.error('Erro ao carregar contratos:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = () => {
    setSelectedContract(null);
    setIsFormOpen(true);
  };

  const handleEdit = (contract: Contract) => {
    setSelectedContract(contract);
    setIsFormOpen(true);
  };

  const handleSubmit = async (data: any) => {
    try {
      if (selectedContract) {
        await contractManagementService.updateContract(selectedContract.id, data);
      } else {
        await contractManagementService.createContract({
          companyId: 'comp-001',
          ...data,
          documents: [],
          milestones: [],
          alerts: [],
        });
      }
      await loadData();
    } catch (err) {
      throw err;
    }
  };

  const getStatusBadge = (status: string) => {
    const variants: Record<string, any> = {
      active: 'default',
      draft: 'secondary',
      expired: 'destructive',
      cancelled: 'destructive',
    };
    const labels: Record<string, string> = {
      active: 'Ativo',
      draft: 'Rascunho',
      expired: 'Expirado',
      cancelled: 'Cancelado',
    };
    return <Badge variant={variants[status] || 'secondary'}>{labels[status] || status}</Badge>;
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Building2 className="h-12 w-12 animate-pulse text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <Building2 className="h-6 w-6 text-primary" />
            Gestão de Contratos
          </h2>
          <p className="text-muted-foreground mt-1">Gerencie contratos e compromissos financeiros</p>
        </div>
        <Button className="gap-2" onClick={handleCreate}>
          <Plus className="h-4 w-4" />
          Novo Contrato
        </Button>
      </div>

      {summary && (
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Total de Contratos</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{summary.totalContracts}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Contratos Ativos</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{summary.activeContracts}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Valor Total</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(summary.totalValue)}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Compromisso Mensal</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(summary.monthlyCommitment)}</div>
            </CardContent>
          </Card>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Contratos</CardTitle>
          <CardDescription>Lista de todos os contratos cadastrados</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {contracts.map((contract) => (
              <Card key={contract.id}>
                <CardContent className="pt-6">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <h4 className="font-semibold">{contract.name}</h4>
                        {getStatusBadge(contract.status)}
                      </div>
                      <p className="text-sm text-muted-foreground mb-3">{contract.counterparty}</p>
                      <div className="grid grid-cols-2 gap-4 text-sm">
                        <div className="flex items-center gap-2">
                          <DollarSign className="h-4 w-4 text-muted-foreground" />
                          <span>{formatCurrency(contract.value)}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <Calendar className="h-4 w-4 text-muted-foreground" />
                          <span>
                            {contract.startDate.toLocaleDateString()} - {contract.endDate.toLocaleDateString()}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <Button variant="ghost" size="icon" onClick={() => handleEdit(contract)}>
                        <Edit className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </CardContent>
      </Card>

      {summary && summary.upcomingPayments.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertCircle className="h-5 w-5 text-yellow-500" />
              Próximos Pagamentos
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {summary.upcomingPayments.map((payment, index) => (
                <div key={index} className="flex items-center justify-between p-3 bg-muted rounded-lg">
                  <div>
                    <p className="font-medium">{payment.contractName}</p>
                    <p className="text-sm text-muted-foreground">
                      Vence em {payment.dueDate.toLocaleDateString()}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-semibold">{formatCurrency(payment.amount)}</p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <ContractForm
        open={isFormOpen}
        onOpenChange={setIsFormOpen}
        onSubmit={handleSubmit}
        contract={selectedContract}
      />
    </div>
  );
}
