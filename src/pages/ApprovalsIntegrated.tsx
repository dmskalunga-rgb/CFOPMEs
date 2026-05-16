import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { CheckCircle, XCircle, Clock, Filter, Search } from 'lucide-react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { formatCurrency, formatDate } from '@/lib/index';
import { integratedServices } from '@/services/integratedServices';
import { supabase } from '@/integrations/supabase/client';
import { springPresets, staggerContainer, staggerItem } from '@/lib/motion';

export default function Approvals() {
  const { toast } = useToast();
  const [approvals, setApprovals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('PENDING');
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedApproval, setSelectedApproval] = useState<any>(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [action, setAction] = useState<'APPROVE' | 'REJECT'>('APPROVE');
  const [comments, setComments] = useState('');

  useEffect(() => {
    loadApprovals();
  }, []);

  const loadApprovals = async () => {
    try {
      setLoading(true);
      const { data: user } = await supabase.auth.getUser();

      if (!user?.user) {
        toast({ title: 'Erro', description: 'Usuário não autenticado', variant: 'destructive' });
        return;
      }

      // Buscar aprovações onde o usuário é aprovador ou solicitante
      const { data, error } = await supabase
        .from('approval_requests')
        .select('*')
        .or(`approver_id.eq.${user.user.id},requester_id.eq.${user.user.id}`)
        .order('requested_at', { ascending: false });

      if (error) throw error;
      setApprovals(data || []);
    } catch (error: any) {
      console.error('Erro ao carregar aprovações:', error);
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async () => {
    if (!selectedApproval) return;

    try {
      const { data: user } = await supabase.auth.getUser();
      if (!user?.user) return;

      await integratedServices.approvals.approveRequest(
        selectedApproval.id,
        user.user.id,
        comments
      );

      toast({ title: 'Sucesso', description: 'Solicitação aprovada' });
      setIsDialogOpen(false);
      setComments('');
      loadApprovals();
    } catch (error: any) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    }
  };

  const handleReject = async () => {
    if (!selectedApproval || !comments) {
      toast({ title: 'Erro', description: 'Motivo da rejeição é obrigatório', variant: 'destructive' });
      return;
    }

    try {
      const { data: user } = await supabase.auth.getUser();
      if (!user?.user) return;

      await integratedServices.approvals.rejectRequest(selectedApproval.id, user.user.id, comments);

      toast({ title: 'Sucesso', description: 'Solicitação rejeitada' });
      setIsDialogOpen(false);
      setComments('');
      loadApprovals();
    } catch (error: any) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    }
  };

  const openDialog = (approval: any, actionType: 'APPROVE' | 'REJECT') => {
    setSelectedApproval(approval);
    setAction(actionType);
    setIsDialogOpen(true);
  };

  const filteredApprovals = approvals.filter((a) => {
    if (filter !== 'ALL' && a.status !== filter) return false;
    if (searchTerm && !a.description.toLowerCase().includes(searchTerm.toLowerCase())) return false;
    return true;
  });

  const pendingCount = approvals.filter((a) => a.status === 'PENDING').length;

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'PENDING':
        return 'default';
      case 'APPROVED':
        return 'default';
      case 'REJECTED':
        return 'destructive';
      case 'CANCELLED':
        return 'secondary';
      default:
        return 'default';
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'PENDING':
        return <Clock className="h-4 w-4" />;
      case 'APPROVED':
        return <CheckCircle className="h-4 w-4 text-green-600" />;
      case 'REJECTED':
        return <XCircle className="h-4 w-4 text-destructive" />;
      default:
        return <Clock className="h-4 w-4" />;
    }
  };

  const getTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      INVOICE: 'Fatura',
      TRANSACTION: 'Transação',
      PAYROLL: 'Payroll',
      EXPENSE: 'Despesa',
      PURCHASE: 'Compra',
      CONTRACT: 'Contrato',
      OTHER: 'Outro',
    };
    return labels[type] || type;
  };

  return (
    <Layout>
      <motion.div
        className="space-y-6"
        variants={staggerContainer}
        initial="initial"
        animate="animate"
      >
        {/* Header */}
        <motion.div variants={staggerItem} className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-2">
              Aprovações
              {pendingCount > 0 && (
                <Badge variant="destructive" className="ml-2">
                  {pendingCount} Pendentes
                </Badge>
              )}
            </h1>
            <p className="text-muted-foreground">Gerencie solicitações de aprovação</p>
          </div>
        </motion.div>

        {/* Filters */}
        <motion.div variants={staggerItem} className="flex gap-4">
          <div className="flex-1">
            <Input
              placeholder="Buscar aprovações..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="max-w-sm"
            />
          </div>
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">Todas</SelectItem>
              <SelectItem value="PENDING">Pendentes</SelectItem>
              <SelectItem value="APPROVED">Aprovadas</SelectItem>
              <SelectItem value="REJECTED">Rejeitadas</SelectItem>
            </SelectContent>
          </Select>
        </motion.div>

        {/* Stats */}
        <motion.div variants={staggerItem} className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Pendentes</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {approvals.filter((a) => a.status === 'PENDING').length}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Aprovadas</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {approvals.filter((a) => a.status === 'APPROVED').length}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Rejeitadas</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-destructive">
                {approvals.filter((a) => a.status === 'REJECTED').length}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">Total</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{approvals.length}</div>
            </CardContent>
          </Card>
        </motion.div>

        {/* Approvals List */}
        <motion.div variants={staggerItem} className="space-y-4">
          {loading ? (
            <Card>
              <CardContent className="py-12 text-center">
                <p className="text-muted-foreground">Carregando aprovações...</p>
              </CardContent>
            </Card>
          ) : filteredApprovals.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center">
                <CheckCircle className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
                <p className="text-muted-foreground">Nenhuma aprovação encontrada</p>
              </CardContent>
            </Card>
          ) : (
            filteredApprovals.map((approval) => (
              <Card key={approval.id}>
                <CardHeader>
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        {getStatusIcon(approval.status)}
                        <Badge variant={getStatusColor(approval.status)}>{approval.status}</Badge>
                        <Badge variant="outline">{getTypeLabel(approval.request_type)}</Badge>
                        {approval.priority === 'HIGH' || approval.priority === 'URGENT' ? (
                          <Badge variant="destructive">{approval.priority}</Badge>
                        ) : null}
                      </div>
                      <CardTitle className="text-base">{approval.description}</CardTitle>
                      {approval.amount && (
                        <p className="text-sm text-muted-foreground mt-1">
                          Valor: {formatCurrency(approval.amount)}
                        </p>
                      )}
                      <p className="text-xs text-muted-foreground mt-2">
                        Solicitado em: {formatDate(approval.requested_at)}
                      </p>
                      {approval.approved_at && (
                        <p className="text-xs text-green-600 mt-1">
                          Aprovado em: {formatDate(approval.approved_at)}
                        </p>
                      )}
                      {approval.rejected_at && (
                        <p className="text-xs text-destructive mt-1">
                          Rejeitado em: {formatDate(approval.rejected_at)}
                          {approval.rejection_reason && ` - ${approval.rejection_reason}`}
                        </p>
                      )}
                    </div>
                    {approval.status === 'PENDING' && (
                      <div className="flex gap-2">
                        <Button
                          variant="default"
                          size="sm"
                          onClick={() => openDialog(approval, 'APPROVE')}
                        >
                          <CheckCircle className="mr-2 h-4 w-4" />
                          Aprovar
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => openDialog(approval, 'REJECT')}
                        >
                          <XCircle className="mr-2 h-4 w-4" />
                          Rejeitar
                        </Button>
                      </div>
                    )}
                  </div>
                </CardHeader>
              </Card>
            ))
          )}
        </motion.div>

        {/* Approval Dialog */}
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {action === 'APPROVE' ? 'Aprovar Solicitação' : 'Rejeitar Solicitação'}
              </DialogTitle>
              <DialogDescription>
                {selectedApproval?.description}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div>
                <Label>
                  {action === 'APPROVE' ? 'Comentários (opcional)' : 'Motivo da Rejeição *'}
                </Label>
                <Textarea
                  value={comments}
                  onChange={(e) => setComments(e.target.value)}
                  placeholder={
                    action === 'APPROVE'
                      ? 'Adicione comentários sobre a aprovação...'
                      : 'Explique o motivo da rejeição...'
                  }
                  rows={4}
                />
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setIsDialogOpen(false)}>
                  Cancelar
                </Button>
                <Button
                  variant={action === 'APPROVE' ? 'default' : 'destructive'}
                  onClick={action === 'APPROVE' ? handleApprove : handleReject}
                >
                  {action === 'APPROVE' ? 'Aprovar' : 'Rejeitar'}
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </motion.div>
    </Layout>
  );
}
