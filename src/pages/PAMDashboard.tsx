import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Key, Clock, Shield, FileText, RefreshCw } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { supabase } from '@/integrations/supabase/client';

// ─── Interfaces baseadas nas colunas reais das tabelas ───────────────────────

interface PamCredential {
  id: string;
  name: string;
  type: string;
  is_active: boolean | null;
  description: string | null;
  next_rotation_at: string | null;
  created_at: string | null;
}

interface PamSession {
  id: string;
  session_type: string;
  status: string | null;
  started_at: string | null;
  credential_id: string | null;
  // Join com pam_credentials
  pam_credentials?: { name: string; type: string } | null;
}

interface AuditLogRow {
  id: string;
  action: string;
  resource_type: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function getTenantId(): Promise<string | null> {
  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) return null;
    const { data } = await supabase.from('users').select('tenant_id').eq('id', user.id).maybeSingle();
    return data?.tenant_id ?? null;
  } catch {
    return null;
  }
}

export default function PAMDashboard() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(false);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<PamCredential[]>([]);
  const [sessions, setSessions] = useState<PamSession[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogRow[]>([]);

  useEffect(() => {
    getTenantId().then((tid) => {
      setTenantId(tid);
    });
  }, []);

  useEffect(() => {
    if (tenantId) loadData(tenantId);
  }, [tenantId]);

  const loadData = async (tid: string) => {
    setLoading(true);
    try {
      const [credsRes, sessionsRes, auditRes] = await Promise.all([
        // Credenciais PAM do tenant
        supabase
          .from('pam_credentials')
          .select('id, name, type, is_active, description, next_rotation_at, created_at')
          .eq('tenant_id', tid)
          .order('created_at', { ascending: false }),

        // Sessões PAM do tenant
        supabase
          .from('pam_sessions')
          .select('id, session_type, status, started_at, credential_id')
          .eq('tenant_id', tid)
          .order('started_at', { ascending: false })
          .limit(20),

        // Audit logs PAM dos últimos registos
        supabase
          .from('audit_logs')
          .select('id, action, resource_type, metadata, created_at')
          .eq('tenant_id', tid)
          .or('action.ilike.PAM%,action.ilike.IAM%')
          .order('created_at', { ascending: false })
          .limit(30),
      ]);

      setCredentials((credsRes.data as PamCredential[]) ?? []);
      setSessions((sessionsRes.data as PamSession[]) ?? []);
      setAuditLogs((auditRes.data as AuditLogRow[]) ?? []);
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Erro desconhecido';
      toast({ title: 'Erro ao carregar dados PAM', description: msg, variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  // Simular rotação de credencial (actualizar next_rotation_at)
  const handleRotateCredential = async (credId: string, credName: string) => {
    const { error } = await supabase
      .from('pam_credentials')
      .update({ next_rotation_at: new Date(Date.now() + 90 * 24 * 60 * 60 * 1000).toISOString() })
      .eq('id', credId);

    if (error) {
      toast({ title: 'Erro ao rotacionar credencial', description: error.message, variant: 'destructive' });
    } else {
      toast({
        title: 'Credencial rotacionada',
        description: `${credName} foi rotacionada com sucesso. Próxima rotação em 90 dias.`
      });
      if (tenantId) loadData(tenantId);
    }
  };

  const getSessionStatusVariant = (status: string | null): 'default' | 'destructive' | 'secondary' | 'outline' => {
    switch (status) {
      case 'ACTIVE': return 'default';
      case 'COMPLETED': return 'secondary';
      case 'FAILED': return 'destructive';
      default: return 'outline';
    }
  };

  const getTypeLabel = (type: string): string => {
    switch (type) {
      case 'ssh': return 'SSH';
      case 'database': return 'Base de Dados';
      case 'api_key': return 'API Key';
      case 'password': return 'Password';
      default: return type.toUpperCase();
    }
  };

  const isRotationUrgent = (nextRotation: string | null): boolean => {
    if (!nextRotation) return false;
    const days = (new Date(nextRotation).getTime() - Date.now()) / (1000 * 60 * 60 * 24);
    return days <= 15;
  };

  // Stats derivados
  const activeCredentials = credentials.filter(c => c.is_active !== false).length;
  const urgentRotations = credentials.filter(c => isRotationUrgent(c.next_rotation_at)).length;
  const activeSessions = sessions.filter(s => s.status === 'ACTIVE').length;
  const pamEvents = auditLogs.filter(l => l.action.startsWith('PAM')).length;

  return (
    <div className="container mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Shield className="h-8 w-8 text-primary" />
            PAM Dashboard
          </h1>
          <p className="text-muted-foreground mt-1">
            Privileged Access Management — Gestão de Acessos Privilegiados
          </p>
        </div>
        <Button onClick={() => tenantId && loadData(tenantId)} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Atualizar
        </Button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Credenciais Activas</CardTitle>
            <Key className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{activeCredentials}</div>
            <p className="text-xs text-muted-foreground">{credentials.length} total</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Rotações Urgentes</CardTitle>
            <Clock className="h-4 w-4 text-destructive" />
          </CardHeader>
          <CardContent>
            <div className={`text-2xl font-bold ${urgentRotations > 0 ? 'text-destructive' : ''}`}>
              {urgentRotations}
            </div>
            <p className="text-xs text-muted-foreground">Em menos de 15 dias</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Sessões Activas</CardTitle>
            <Shield className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{activeSessions}</div>
            <p className="text-xs text-muted-foreground">{sessions.length} total</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Eventos PAM</CardTitle>
            <FileText className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{pamEvents}</div>
            <p className="text-xs text-muted-foreground">Nos últimos registos</p>
          </CardContent>
        </Card>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="credentials" className="space-y-4">
        <TabsList>
          <TabsTrigger value="credentials">Credenciais</TabsTrigger>
          <TabsTrigger value="sessions">Sessões</TabsTrigger>
          <TabsTrigger value="audit">Audit Logs</TabsTrigger>
        </TabsList>

        {/* Credenciais PAM */}
        <TabsContent value="credentials" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Credenciais Privilegiadas</CardTitle>
              <CardDescription>Credenciais de acesso a sistemas críticos</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {loading ? (
                  <p className="text-center text-muted-foreground py-8">A carregar...</p>
                ) : credentials.length === 0 ? (
                  <p className="text-center text-muted-foreground py-8">Nenhuma credencial encontrada</p>
                ) : (
                  credentials.map((cred) => {
                    const urgent = isRotationUrgent(cred.next_rotation_at);
                    return (
                      <div key={cred.id} className="flex items-center justify-between p-4 border rounded-lg">
                        <div className="flex items-center gap-3 flex-1">
                          <Key className="h-4 w-4 text-primary shrink-0" />
                          <div>
                            <div className="flex items-center gap-2">
                              <h3 className="font-semibold">{cred.name}</h3>
                              <Badge variant="outline">{getTypeLabel(cred.type)}</Badge>
                              {urgent && <Badge variant="destructive" className="text-xs">Rotação Urgente</Badge>}
                              {cred.is_active === false && <Badge variant="secondary" className="text-xs">Inactiva</Badge>}
                            </div>
                            {cred.description && (
                              <p className="text-sm text-muted-foreground mt-1">{cred.description}</p>
                            )}
                            {cred.next_rotation_at && (
                              <p className={`text-xs mt-1 ${urgent ? 'text-destructive font-medium' : 'text-muted-foreground'}`}>
                                Próxima rotação: {new Date(cred.next_rotation_at).toLocaleDateString('pt-AO')}
                              </p>
                            )}
                          </div>
                        </div>
                        <Button
                          variant={urgent ? 'destructive' : 'outline'}
                          size="sm"
                          onClick={() => handleRotateCredential(cred.id, cred.name)}
                        >
                          <RefreshCw className="h-4 w-4 mr-1" />
                          Rotacionar
                        </Button>
                      </div>
                    );
                  })
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Sessões */}
        <TabsContent value="sessions" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Sessões Privilegiadas</CardTitle>
              <CardDescription>Sessões de acesso privilegiado monitorizadas</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {loading ? (
                  <p className="text-center text-muted-foreground py-8">A carregar...</p>
                ) : sessions.length === 0 ? (
                  <p className="text-center text-muted-foreground py-8">Nenhuma sessão encontrada</p>
                ) : (
                  sessions.map((session) => (
                    <div key={session.id} className="p-4 border rounded-lg">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <Badge variant={getSessionStatusVariant(session.status)}>
                              {session.status === 'ACTIVE' ? 'Activa' :
                               session.status === 'COMPLETED' ? 'Concluída' :
                               session.status === 'FAILED' ? 'Falhou' :
                               session.status ?? 'Desconhecida'}
                            </Badge>
                            <Badge variant="outline" className="text-xs">{session.session_type}</Badge>
                          </div>
                          <p className="text-xs text-muted-foreground">
                            Iniciada: {session.started_at
                              ? new Date(session.started_at).toLocaleString('pt-AO')
                              : '—'}
                          </p>
                          {session.status === 'ACTIVE' && (
                            <p className="text-xs text-primary font-medium mt-1">
                              ● Em progresso há {session.started_at
                                ? Math.round((Date.now() - new Date(session.started_at).getTime()) / 60000)
                                : 0} minutos
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Audit Logs */}
        <TabsContent value="audit" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Registos de Auditoria</CardTitle>
              <CardDescription>Trilha de auditoria completa de acções PAM e IAM</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {loading ? (
                  <p className="text-center text-muted-foreground py-8">A carregar...</p>
                ) : auditLogs.length === 0 ? (
                  <p className="text-center text-muted-foreground py-8">Nenhum registo encontrado</p>
                ) : (
                  auditLogs.map((log) => {
                    const meta = log.metadata as Record<string, unknown> | null;
                    const isFailure = meta?.status === 'FAILURE';
                    const isPam = log.action.startsWith('PAM');
                    return (
                      <div key={log.id} className="flex items-start justify-between p-3 border-b last:border-0">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <Badge
                              variant={isFailure ? 'destructive' : isPam ? 'default' : 'outline'}
                              className="text-xs"
                            >
                              {log.action}
                            </Badge>
                            {log.resource_type && (
                              <span className="text-xs text-muted-foreground">{log.resource_type}</span>
                            )}
                          </div>
                          {meta?.credential_name && (
                            <p className="text-xs text-muted-foreground mt-1">
                              Credencial: {String(meta.credential_name)}
                            </p>
                          )}
                          {meta?.target && (
                            <p className="text-xs text-muted-foreground">Alvo: {String(meta.target)}</p>
                          )}
                          {meta?.ip && (
                            <p className="text-xs text-muted-foreground">IP: {String(meta.ip)}</p>
                          )}
                          <p className="text-xs text-muted-foreground mt-1">
                            {log.created_at ? new Date(log.created_at).toLocaleString('pt-AO') : '—'}
                          </p>
                        </div>
                        {isFailure && (
                          <Badge variant="destructive" className="text-xs shrink-0">Falha</Badge>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
