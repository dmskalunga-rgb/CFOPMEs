// Approvals Page - Versão Completa e Funcional
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CheckCircle, XCircle, Clock, FileText, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

interface Approval {
  id: string;
  type: 'expense' | 'invoice' | 'leave' | 'purchase' | 'other';
  title: string;
  requester: string;
  amount?: number;
  description: string;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
  reviewed_at?: string;
  reviewer?: string;
}

const generateMockApprovals = (): Approval[] => {
  const types: Approval['type'][] = ['expense', 'invoice', 'leave', 'purchase', 'other'];
  const statuses: Approval['status'][] = ['pending', 'approved', 'rejected'];
  const requesters = ['João Silva', 'Maria Santos', 'Pedro Costa', 'Ana Oliveira', 'Carlos Mendes'];
  
  return Array.from({ length: 25 }, (_, i) => {
    const status = i < 8 ? 'pending' : statuses[Math.floor(Math.random() * statuses.length)];
    const type = types[Math.floor(Math.random() * types.length)];
    
    return {
      id: `app-${i + 1}`,
      type,
      title: `Solicitação ${i + 1}`,
      requester: requesters[Math.floor(Math.random() * requesters.length)],
      amount: ['expense', 'invoice', 'purchase'].includes(type) ? Math.floor(Math.random() * 500000) + 10000 : undefined,
      description: 'Descrição da solicitação',
      status,
      created_at: new Date(Date.now() - Math.random() * 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
      reviewed_at: status !== 'pending' ? new Date(Date.now() - Math.random() * 15 * 24 * 60 * 60 * 1000).toISOString().split('T')[0] : undefined,
      reviewer: status !== 'pending' ? 'Administrador' : undefined
    };
  }).sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
};

export default function Approvals() {
  const [approvals, setApprovals] = useState<Approval[]>(generateMockApprovals());
  const [activeTab, setActiveTab] = useState('pending');

  const pendingApprovals = approvals.filter(a => a.status === 'pending');
  const approvedApprovals = approvals.filter(a => a.status === 'approved');
  const rejectedApprovals = approvals.filter(a => a.status === 'rejected');

  const handleApprove = (id: string) => {
    const updated = approvals.map(a =>
      a.id === id
        ? {
            ...a,
            status: 'approved' as const,
            reviewed_at: new Date().toISOString().split('T')[0],
            reviewer: 'Administrador'
          }
        : a
    );
    setApprovals(updated);
    toast.success('Solicitação aprovada!');
  };

  const handleReject = (id: string) => {
    const updated = approvals.map(a =>
      a.id === id
        ? {
            ...a,
            status: 'rejected' as const,
            reviewed_at: new Date().toISOString().split('T')[0],
            reviewer: 'Administrador'
          }
        : a
    );
    setApprovals(updated);
    toast.success('Solicitação rejeitada!');
  };

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat('pt-AO', {
      style: 'currency',
      currency: 'AOA',
      minimumFractionDigits: 0
    }).format(value);
  };

  const getTypeBadge = (type: Approval['type']) => {
    const variants = {
      expense: { label: 'Despesa', variant: 'default' as const },
      invoice: { label: 'Fatura', variant: 'secondary' as const },
      leave: { label: 'Ausência', variant: 'outline' as const },
      purchase: { label: 'Compra', variant: 'default' as const },
      other: { label: 'Outro', variant: 'secondary' as const }
    };
    return variants[type];
  };

  const renderApprovalList = (list: Approval[]) => (
    <div className="space-y-4">
      {list.map((approval) => (
        <div key={approval.id} className="flex items-center justify-between border-b pb-4 last:border-0">
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                <FileText className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="font-medium">{approval.title}</p>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <span>{approval.requester}</span>
                  <span>•</span>
                  <Badge variant={getTypeBadge(approval.type).variant}>
                    {getTypeBadge(approval.type).label}
                  </Badge>
                  {approval.amount && (
                    <>
                      <span>•</span>
                      <span className="font-medium">{formatCurrency(approval.amount)}</span>
                    </>
                  )}
                  <span>•</span>
                  <span>{new Date(approval.created_at).toLocaleDateString('pt-AO')}</span>
                </div>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {approval.status === 'pending' ? (
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => handleApprove(approval.id)}>
                  <CheckCircle className="h-4 w-4 mr-2 text-green-600" />
                  Aprovar
                </Button>
                <Button variant="outline" size="sm" onClick={() => handleReject(approval.id)}>
                  <XCircle className="h-4 w-4 mr-2 text-red-600" />
                  Rejeitar
                </Button>
              </div>
            ) : (
              <div className="text-right">
                <p className="text-sm font-medium">
                  {approval.status === 'approved' ? 'Aprovado' : 'Rejeitado'}
                </p>
                <p className="text-xs text-muted-foreground">
                  por {approval.reviewer} em {new Date(approval.reviewed_at!).toLocaleDateString('pt-AO')}
                </p>
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Aprovações</h1>
            <p className="text-muted-foreground">Sistema de aprovações e solicitações</p>
          </div>
          <Button variant="outline" onClick={() => setApprovals(generateMockApprovals())}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Atualizar
          </Button>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total</CardTitle>
              <FileText className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{approvals.length}</div>
              <p className="text-xs text-muted-foreground">solicitações</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Pendentes</CardTitle>
              <Clock className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-yellow-600">{pendingApprovals.length}</div>
              <p className="text-xs text-muted-foreground">aguardando</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Aprovadas</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{approvedApprovals.length}</div>
              <p className="text-xs text-muted-foreground">aprovadas</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Rejeitadas</CardTitle>
              <XCircle className="h-4 w-4 text-red-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">{rejectedApprovals.length}</div>
              <p className="text-xs text-muted-foreground">rejeitadas</p>
            </CardContent>
          </Card>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="pending">
              Pendentes ({pendingApprovals.length})
            </TabsTrigger>
            <TabsTrigger value="approved">
              Aprovadas ({approvedApprovals.length})
            </TabsTrigger>
            <TabsTrigger value="rejected">
              Rejeitadas ({rejectedApprovals.length})
            </TabsTrigger>
          </TabsList>

          <TabsContent value="pending">
            <Card>
              <CardHeader>
                <CardTitle>Solicitações Pendentes</CardTitle>
                <CardDescription>Solicitações aguardando aprovação</CardDescription>
              </CardHeader>
              <CardContent>
                {pendingApprovals.length > 0 ? (
                  renderApprovalList(pendingApprovals)
                ) : (
                  <p className="text-center text-muted-foreground py-8">
                    Nenhuma solicitação pendente
                  </p>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="approved">
            <Card>
              <CardHeader>
                <CardTitle>Solicitações Aprovadas</CardTitle>
                <CardDescription>Histórico de aprovações</CardDescription>
              </CardHeader>
              <CardContent>
                {approvedApprovals.length > 0 ? (
                  renderApprovalList(approvedApprovals)
                ) : (
                  <p className="text-center text-muted-foreground py-8">
                    Nenhuma solicitação aprovada
                  </p>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="rejected">
            <Card>
              <CardHeader>
                <CardTitle>Solicitações Rejeitadas</CardTitle>
                <CardDescription>Histórico de rejeições</CardDescription>
              </CardHeader>
              <CardContent>
                {rejectedApprovals.length > 0 ? (
                  renderApprovalList(rejectedApprovals)
                ) : (
                  <p className="text-center text-muted-foreground py-8">
                    Nenhuma solicitação rejeitada
                  </p>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
