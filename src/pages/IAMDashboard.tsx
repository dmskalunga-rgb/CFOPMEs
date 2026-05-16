import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Shield, Users, AlertTriangle, Activity, Lock, RefreshCw, Key,
  CheckCircle, XCircle, Monitor, Smartphone, Search, Bell,
  UserCheck, UserX, Clock, MapPin, Globe, Laptop
} from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { supabase } from '@/integrations/supabase/client';

// ─── Interfaces baseadas nas colunas reais do Supabase ────────────────────────

interface UserRow {
  id: string;
  full_name: string;
  email: string;
  role: string | null;
  department: string | null;
  position: string | null;
  is_active: boolean | null;
  last_login_at: string | null;
  last_login_ip: string | null;
  two_factor_enabled: boolean | null;
  created_at: string | null;
}

interface RoleRow {
  id: string;
  name: string;
  display_name: string | null;
  description: string | null;
  is_system: boolean | null;
  _perm_count?: number;
}

interface AuditLogRow {
  id: string;
  user_id: string | null;
  action: string;
  resource_type: string;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
}

interface SessionRow {
  id: string;
  user_id: string;
  session_token: string;
  ip_address: string | null;
  user_agent: string | null;
  device_type: string | null;
  location: string | null;
  is_active: boolean | null;
  last_activity_at: string | null;
  expires_at: string | null;
  created_at: string | null;
}

interface NotificationRow {
  id: string;
  user_id: string | null;
  type: string;
  title: string;
  message: string;
  is_read: boolean | null;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
}

// ─── Helper: obter tenant_id do utilizador autenticado ───────────────────────

async function getTenantId(): Promise<string | null> {
  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) return null;
    const { data } = await supabase
      .from('users')
      .select('tenant_id')
      .eq('id', user.id)
      .maybeSingle();
    return data?.tenant_id ?? null;
  } catch {
    return null;
  }
}

// ─── Helpers visuais ─────────────────────────────────────────────────────────

function getActionColor(action: string): 'default' | 'destructive' | 'secondary' | 'outline' {
  if (action.includes('FAILED') || action.includes('DENIED') || action.includes('SUSPEND')) return 'destructive';
  if (action.includes('MFA') || action.includes('2FA')) return 'default';
  if (action.includes('LOGIN') && !action.includes('FAILED')) return 'outline';
  return 'secondary';
}

function getActionIcon(action: string) {
  if (action.includes('FAILED') || action.includes('DENIED')) return <XCircle className="h-3.5 w-3.5 text-destructive" />;
  if (action.includes('LOGIN') && !action.includes('FAILED')) return <CheckCircle className="h-3.5 w-3.5 text-green-600" />;
  if (action.includes('SUSPEND')) return <UserX className="h-3.5 w-3.5 text-orange-500" />;
  if (action.includes('ACTIV')) return <UserCheck className="h-3.5 w-3.5 text-green-600" />;
  if (action.includes('ROLE')) return <Shield className="h-3.5 w-3.5 text-blue-500" />;
  if (action.includes('MFA') || action.includes('2FA')) return <Lock className="h-3.5 w-3.5 text-purple-500" />;
  return <Activity className="h-3.5 w-3.5 text-muted-foreground" />;
}

function getDeviceIcon(deviceType: string | null) {
  switch ((deviceType ?? '').toUpperCase()) {
    case 'MOBILE':  return <Smartphone className="h-4 w-4" />;
    case 'DESKTOP': return <Laptop className="h-4 w-4" />;
    default:        return <Monitor className="h-4 w-4" />;
  }
}

function getNotifTypeColor(type: string): string {
  switch (type.toUpperCase()) {
    case 'WARNING': return 'border-orange-300 bg-orange-50 dark:border-orange-700 dark:bg-orange-950';
    case 'ERROR':   return 'border-destructive/30 bg-destructive/10';
    case 'SUCCESS': return 'border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950';
    default:        return 'border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950';
  }
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '—';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins  = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days  = Math.floor(diff / 86400000);
  if (mins  < 1)   return 'agora mesmo';
  if (mins  < 60)  return `${mins}m atrás`;
  if (hours < 24)  return `${hours}h atrás`;
  if (days  < 30)  return `${days}d atrás`;
  return new Date(dateStr).toLocaleDateString('pt-AO');
}

// ─── Componente principal ─────────────────────────────────────────────────────

export default function IAMDashboard() {
  const { toast } = useToast();

  const [loading,       setLoading]       = useState(false);
  const [tenantId,      setTenantId]      = useState<string | null>(null);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);

  // Dados do Supabase
  const [users,         setUsers]         = useState<UserRow[]>([]);
  const [roles,         setRoles]         = useState<RoleRow[]>([]);
  const [auditLogs,     setAuditLogs]     = useState<AuditLogRow[]>([]);
  const [sessions,      setSessions]      = useState<SessionRow[]>([]);
  const [notifications, setNotifications] = useState<NotificationRow[]>([]);

  // UI
  const [searchUsers,   setSearchUsers]   = useState('');
  const [filterRole,    setFilterRole]    = useState('all');
  const [filterStatus,  setFilterStatus]  = useState('all');
  const [searchAudit,   setSearchAudit]   = useState('');
  const [filterAudit,   setFilterAudit]   = useState('all');

  // ─── Inicialização ──────────────────────────────────────────────────────

  useEffect(() => {
    const init = async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (user) setCurrentUserId(user.id);
      const tid = await getTenantId();
      setTenantId(tid);
    };
    init();
  }, []);

  useEffect(() => {
    if (tenantId) loadData(tenantId);
  }, [tenantId]);

  // ─── Carregamento de dados ──────────────────────────────────────────────

  const loadData = useCallback(async (tid: string) => {
    setLoading(true);
    try {
      const [usersRes, rolesRes, auditRes, sessionsRes, notifRes] = await Promise.all([

        // Utilizadores do tenant — colunas reais da tabela users
        supabase
          .from('users')
          .select('id, full_name, email, role, department, position, is_active, last_login_at, last_login_ip, two_factor_enabled, created_at')
          .eq('tenant_id', tid)
          .order('full_name'),

        // Roles (do tenant + globais)
        supabase
          .from('roles')
          .select('id, name, display_name, description, is_system')
          .or(`tenant_id.eq.${tid},tenant_id.is.null`)
          .order('name'),

        // Audit logs IAM — últimos 50 eventos
        supabase
          .from('audit_logs')
          .select('id, user_id, action, resource_type, metadata, created_at')
          .eq('tenant_id', tid)
          .ilike('action', 'IAM%')
          .order('created_at', { ascending: false })
          .limit(50),

        // Sessões activas/recentes do tenant (via user_id dos utilizadores do tenant)
        supabase
          .from('user_sessions')
          .select('id, user_id, session_token, ip_address, user_agent, device_type, location, is_active, last_activity_at, expires_at, created_at')
          .eq('tenant_id', tid)
          .order('last_activity_at', { ascending: false })
          .limit(30),

        // Notificações de segurança IAM
        supabase
          .from('notifications')
          .select('id, user_id, type, title, message, is_read, metadata, created_at')
          .eq('tenant_id', tid)
          .order('created_at', { ascending: false })
          .limit(20),
      ]);

      // Erros individuais: log mas não bloquear
      if (usersRes.error)    console.warn('users:', usersRes.error.message);
      if (rolesRes.error)    console.warn('roles:', rolesRes.error.message);
      if (auditRes.error)    console.warn('audit_logs:', auditRes.error.message);
      if (sessionsRes.error) console.warn('user_sessions:', sessionsRes.error.message);
      if (notifRes.error)    console.warn('notifications:', notifRes.error.message);

      const rawRoles = (rolesRes.data ?? []) as RoleRow[];

      // Contar permissões por role via role_permissions
      const rpRes = await supabase
        .from('role_permissions')
        .select('role_id')
        .in('role_id', rawRoles.map(r => r.id));

      const permCountMap: Record<string, number> = {};
      if (!rpRes.error) {
        (rpRes.data ?? []).forEach((rp: { role_id: string }) => {
          permCountMap[rp.role_id] = (permCountMap[rp.role_id] ?? 0) + 1;
        });
      }

      const rolesWithCount = rawRoles.map(r => ({ ...r, _perm_count: permCountMap[r.id] ?? 0 }));

      setUsers((usersRes.data ?? []) as UserRow[]);
      setRoles(rolesWithCount);
      setAuditLogs((auditRes.data ?? []) as AuditLogRow[]);
      setSessions((sessionsRes.data ?? []) as SessionRow[]);
      setNotifications((notifRes.data ?? []) as NotificationRow[]);

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro desconhecido';
      toast({ title: 'Erro ao carregar dados IAM', description: msg, variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  // ─── Acções ─────────────────────────────────────────────────────────────

  const handleToggleUserActive = async (user: UserRow) => {
    const newValue = !user.is_active;
    const { error } = await supabase
      .from('users')
      .update({ is_active: newValue })
      .eq('id', user.id);
    if (error) {
      toast({ title: `Erro ao ${newValue ? 'activar' : 'suspender'} utilizador`, description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: newValue ? 'Utilizador activado' : 'Utilizador suspenso' });
    if (tenantId) loadData(tenantId);
  };

  const handleToggle2FA = async (user: UserRow) => {
    const newValue = !user.two_factor_enabled;
    const { error } = await supabase
      .from('users')
      .update({ two_factor_enabled: newValue })
      .eq('id', user.id);
    if (error) {
      toast({ title: 'Erro ao actualizar 2FA', description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: `2FA ${newValue ? 'activado' : 'desactivado'} para ${user.full_name}` });
    if (tenantId) loadData(tenantId);
  };

  const handleUpdateRole = async (userId: string, newRole: string) => {
    const { error } = await supabase
      .from('users')
      .update({ role: newRole })
      .eq('id', userId);
    if (error) {
      toast({ title: 'Erro ao actualizar role', description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: `Role actualizado para ${newRole}` });
    if (tenantId) loadData(tenantId);
  };

  const handleRevokeSession = async (sessionId: string) => {
    const { error } = await supabase
      .from('user_sessions')
      .update({ is_active: false })
      .eq('id', sessionId);
    if (error) {
      toast({ title: 'Erro ao revogar sessão', description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: 'Sessão revogada com sucesso' });
    if (tenantId) loadData(tenantId);
  };

  const handleMarkNotifRead = async (notifId: string) => {
    await supabase
      .from('notifications')
      .update({ is_read: true, read_at: new Date().toISOString() })
      .eq('id', notifId);
    if (tenantId) loadData(tenantId);
  };

  // ─── Dados computados ────────────────────────────────────────────────────

  const activeUsers    = users.filter(u => u.is_active !== false).length;
  const inactiveUsers  = users.filter(u => u.is_active === false).length;
  const mfaEnabled     = users.filter(u => u.two_factor_enabled).length;
  const activeSessions = sessions.filter(s => s.is_active).length;
  const auditFailures  = auditLogs.filter(l => {
    const m = l.metadata as Record<string, unknown> | null;
    return m?.status === 'FAILURE' || l.action.includes('FAILED') || l.action.includes('DENIED');
  }).length;
  const unreadNotifs   = notifications.filter(n => !n.is_read).length;

  // Filtros de utilizadores
  const filteredUsers = users.filter(u => {
    const matchSearch = !searchUsers ||
      u.full_name.toLowerCase().includes(searchUsers.toLowerCase()) ||
      u.email.toLowerCase().includes(searchUsers.toLowerCase()) ||
      (u.department ?? '').toLowerCase().includes(searchUsers.toLowerCase());
    const matchRole   = filterRole === 'all' || u.role === filterRole;
    const matchStatus = filterStatus === 'all' ||
      (filterStatus === 'active'   && u.is_active !== false) ||
      (filterStatus === 'inactive' && u.is_active === false);
    return matchSearch && matchRole && matchStatus;
  });

  // Filtros de audit
  const filteredAudit = auditLogs.filter(l => {
    const meta = l.metadata as Record<string, unknown> | null;
    const matchSearch = !searchAudit || l.action.toLowerCase().includes(searchAudit.toLowerCase());
    const matchFilter = filterAudit === 'all' ||
      (filterAudit === 'failure' && (meta?.status === 'FAILURE' || l.action.includes('FAILED') || l.action.includes('DENIED'))) ||
      (filterAudit === 'success' && meta?.status === 'SUCCESS') ||
      (filterAudit === 'auth'    && l.resource_type === 'AUTH') ||
      (filterAudit === 'user'    && l.resource_type === 'USER');
    return matchSearch && matchFilter;
  });

  // Contagem de utilizadores por role para o gráfico de roles
  const usersByRole: Record<string, number> = {};
  users.forEach(u => {
    const r = u.role ?? 'Sem role';
    usersByRole[r] = (usersByRole[r] ?? 0) + 1;
  });

  // Utilizador para nome numa sessão
  const userById = (uid: string) => users.find(u => u.id === uid);

  // Roles únicos presentes nos utilizadores
  const uniqueRoles = Array.from(new Set(users.map(u => u.role).filter(Boolean))) as string[];

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <div className="container mx-auto p-6 space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Shield className="h-8 w-8 text-primary" />
            IAM Dashboard
          </h1>
          <p className="text-muted-foreground mt-1">
            Identity &amp; Access Management — Gestão de Identidades e Acessos
          </p>
        </div>
        <Button onClick={() => tenantId && loadData(tenantId)} disabled={loading} variant="outline">
          <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Actualizar
        </Button>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-medium">Utilizadores Activos</CardTitle>
            <UserCheck className="h-4 w-4 text-green-600" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-green-600">{activeUsers}</div>
            <p className="text-xs text-muted-foreground">{inactiveUsers} inactivos</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-medium">Roles</CardTitle>
            <Shield className="h-4 w-4 text-primary" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{roles.length}</div>
            <p className="text-xs text-muted-foreground">{roles.filter(r => r.is_system).length} de sistema</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-medium">2FA Activado</CardTitle>
            <Lock className="h-4 w-4 text-purple-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-purple-600">{mfaEnabled}</div>
            <p className="text-xs text-muted-foreground">
              {users.length > 0 ? Math.round((mfaEnabled / users.length) * 100) : 0}% cobertura
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-medium">Sessões Activas</CardTitle>
            <Activity className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-blue-600">{activeSessions}</div>
            <p className="text-xs text-muted-foreground">{sessions.length} total</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-medium">Falhas IAM</CardTitle>
            <AlertTriangle className="h-4 w-4 text-destructive" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-destructive">{auditFailures}</div>
            <p className="text-xs text-muted-foreground">de {auditLogs.length} eventos</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-medium">Alertas</CardTitle>
            <Bell className="h-4 w-4 text-orange-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-orange-600">{unreadNotifs}</div>
            <p className="text-xs text-muted-foreground">não lidos</p>
          </CardContent>
        </Card>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="users" className="space-y-4">
        <TabsList className="flex-wrap">
          <TabsTrigger value="users">Utilizadores ({users.length})</TabsTrigger>
          <TabsTrigger value="roles">Roles ({roles.length})</TabsTrigger>
          <TabsTrigger value="sessions">Sessões ({activeSessions} activas)</TabsTrigger>
          <TabsTrigger value="audit">
            Auditoria IAM
            {auditFailures > 0 && (
              <Badge variant="destructive" className="ml-1 text-xs px-1">{auditFailures}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="notifications">
            Alertas
            {unreadNotifs > 0 && (
              <Badge variant="destructive" className="ml-1 text-xs px-1">{unreadNotifs}</Badge>
            )}
          </TabsTrigger>
        </TabsList>

        {/* ── TAB: UTILIZADORES ──────────────────────────────────────────── */}
        <TabsContent value="users" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Utilizadores da Organização</CardTitle>
              <CardDescription>
                Gestão de identidades — tabela <code className="text-xs">users</code>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Filtros */}
              <div className="flex flex-wrap gap-3">
                <div className="relative flex-1 min-w-[200px]">
                  <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input className="pl-8" placeholder="Pesquisar utilizadores..." value={searchUsers} onChange={e => setSearchUsers(e.target.value)} />
                </div>
                <Select value={filterRole} onValueChange={setFilterRole}>
                  <SelectTrigger className="w-[160px]">
                    <SelectValue placeholder="Filtrar por role" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos os roles</SelectItem>
                    {uniqueRoles.map(r => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Select value={filterStatus} onValueChange={setFilterStatus}>
                  <SelectTrigger className="w-[140px]">
                    <SelectValue placeholder="Estado" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos</SelectItem>
                    <SelectItem value="active">Activos</SelectItem>
                    <SelectItem value="inactive">Inactivos</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {loading ? (
                <div className="text-center py-10 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  <p>A carregar utilizadores...</p>
                </div>
              ) : filteredUsers.length === 0 ? (
                <p className="text-center text-muted-foreground py-10">
                  {users.length === 0 ? 'Nenhum utilizador encontrado no Supabase' : 'Sem resultados para os filtros aplicados'}
                </p>
              ) : (
                <div className="space-y-2">
                  {filteredUsers.map(user => {
                    const isMe = user.id === currentUserId;
                    const roleRow = roles.find(r => r.name === user.role);
                    return (
                      <div
                        key={user.id}
                        className={`p-4 border rounded-lg transition-colors ${isMe ? 'border-primary/30 bg-primary/5' : 'hover:bg-muted/30'}`}
                      >
                        <div className="flex items-start justify-between gap-3 flex-wrap">
                          <div className="flex items-start gap-3">
                            {/* Avatar */}
                            <div className={`h-10 w-10 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0
                              ${user.is_active !== false ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>
                              {(user.full_name || user.email).charAt(0).toUpperCase()}
                            </div>
                            <div>
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="font-semibold">{user.full_name}</span>
                                {isMe && <Badge variant="outline" className="text-xs">Você</Badge>}
                                <Badge variant={user.is_active !== false ? 'default' : 'destructive'} className="text-xs">
                                  {user.is_active !== false ? 'Activo' : 'Inactivo'}
                                </Badge>
                                {user.two_factor_enabled && (
                                  <Badge variant="secondary" className="text-xs">
                                    <Lock className="h-3 w-3 mr-1" />2FA
                                  </Badge>
                                )}
                              </div>
                              <p className="text-sm text-muted-foreground">{user.email}</p>
                              <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground flex-wrap">
                                {user.department && <span>{user.department}</span>}
                                {user.position  && <span>· {user.position}</span>}
                                {user.last_login_at && (
                                  <span className="flex items-center gap-1">
                                    <Clock className="h-3 w-3" />
                                    Último login: {timeAgo(user.last_login_at)}
                                  </span>
                                )}
                                {user.last_login_ip && (
                                  <span className="flex items-center gap-1 font-mono">
                                    <Globe className="h-3 w-3" />
                                    {String(user.last_login_ip)}
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>

                          {/* Acções */}
                          <div className="flex items-center gap-2 flex-wrap">
                            {/* Selector de role */}
                            <div className="flex items-center gap-1.5">
                              <Label className="text-xs text-muted-foreground">Role:</Label>
                              <Select
                                value={user.role ?? 'none'}
                                onValueChange={v => v !== 'none' && handleUpdateRole(user.id, v)}
                              >
                                <SelectTrigger className="h-7 text-xs w-[130px]">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="none">(sem role)</SelectItem>
                                  {roles.map(r => (
                                    <SelectItem key={r.id} value={r.name}>
                                      {r.display_name || r.name}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>

                            {/* Toggle 2FA */}
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 text-xs"
                              onClick={() => handleToggle2FA(user)}
                            >
                              <Lock className="h-3 w-3 mr-1" />
                              {user.two_factor_enabled ? 'Desactivar 2FA' : 'Activar 2FA'}
                            </Button>

                            {/* Toggle activo/suspenso */}
                            {user.is_active !== false ? (
                              <Button
                                variant="destructive"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => handleToggleUserActive(user)}
                                disabled={isMe}
                              >
                                <UserX className="h-3 w-3 mr-1" />
                                Suspender
                              </Button>
                            ) : (
                              <Button
                                variant="default"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => handleToggleUserActive(user)}
                              >
                                <UserCheck className="h-3 w-3 mr-1" />
                                Activar
                              </Button>
                            )}
                          </div>
                        </div>

                        {/* Linha de permissões do role */}
                        {roleRow && roleRow._perm_count != null && roleRow._perm_count > 0 && (
                          <div className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
                            <Key className="h-3 w-3" />
                            <span>
                              <span className="font-medium">{roleRow.display_name || roleRow.name}</span>
                              {' '}— {roleRow._perm_count} permissão{roleRow._perm_count !== 1 ? 'ões' : ''}
                            </span>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Sumário rápido por role */}
              {Object.keys(usersByRole).length > 0 && (
                <div className="pt-4 border-t">
                  <p className="text-xs font-semibold text-muted-foreground mb-2">Distribuição por role:</p>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(usersByRole).map(([role, count]) => (
                      <div key={role} className="flex items-center gap-1.5 px-2 py-1 border rounded-full text-xs">
                        <Shield className="h-3 w-3 text-primary" />
                        <span className="font-medium">{role}</span>
                        <Badge variant="secondary" className="text-xs px-1">{count}</Badge>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── TAB: ROLES ─────────────────────────────────────────────────── */}
        <TabsContent value="roles" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Roles e Permissões</CardTitle>
              <CardDescription>
                Funções de acesso — tabela <code className="text-xs">roles</code>
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="text-center py-10 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  A carregar roles...
                </div>
              ) : roles.length === 0 ? (
                <p className="text-center text-muted-foreground py-10">
                  Nenhuma role encontrada no Supabase
                </p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {roles.map(role => {
                    const usersWithRole = users.filter(u => u.role === role.name).length;
                    return (
                      <div key={role.id} className="p-4 border rounded-lg space-y-3">
                        <div className="flex items-start justify-between">
                          <div className="flex items-center gap-2">
                            <div className="h-9 w-9 rounded-lg bg-primary/10 flex items-center justify-center">
                              <Shield className="h-5 w-5 text-primary" />
                            </div>
                            <div>
                              <div className="flex items-center gap-1.5">
                                <span className="font-semibold">{role.display_name || role.name}</span>
                                {role.is_system && <Badge variant="secondary" className="text-xs">Sistema</Badge>}
                              </div>
                              <p className="text-xs font-mono text-muted-foreground">{role.name}</p>
                            </div>
                          </div>
                          <div className="text-right text-xs text-muted-foreground">
                            <p className="font-medium">{usersWithRole} utilizador{usersWithRole !== 1 ? 'es' : ''}</p>
                            <p>{role._perm_count ?? 0} permissões</p>
                          </div>
                        </div>
                        {role.description && (
                          <p className="text-sm text-muted-foreground">{role.description}</p>
                        )}
                        {/* Barra de progresso de permissões */}
                        <div className="space-y-1">
                          <div className="flex justify-between text-xs text-muted-foreground">
                            <span>Permissões atribuídas</span>
                            <span>{role._perm_count ?? 0}</span>
                          </div>
                          <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                            <div
                              className="h-full bg-primary rounded-full transition-all"
                              style={{ width: `${Math.min(100, ((role._perm_count ?? 0) / 17) * 100)}%` }}
                            />
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── TAB: SESSÕES ───────────────────────────────────────────────── */}
        <TabsContent value="sessions" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Sessões de Utilizadores</CardTitle>
              <CardDescription>
                Sessões activas e recentes — tabela <code className="text-xs">user_sessions</code>
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="text-center py-10 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  A carregar sessões...
                </div>
              ) : sessions.length === 0 ? (
                <p className="text-center text-muted-foreground py-10">
                  Nenhuma sessão encontrada no Supabase
                </p>
              ) : (
                <div className="space-y-2">
                  {/* Activas primeiro */}
                  {[...sessions]
                    .sort((a, b) => (b.is_active ? 1 : 0) - (a.is_active ? 1 : 0))
                    .map(session => {
                      const sessionUser = userById(session.user_id);
                      const isExpired = session.expires_at && new Date(session.expires_at) < new Date();
                      return (
                        <div
                          key={session.id}
                          className={`p-3 border rounded-lg flex items-center justify-between gap-3 flex-wrap
                            ${session.is_active && !isExpired ? 'border-green-200 bg-green-50/50 dark:border-green-900 dark:bg-green-950/30' : 'opacity-70'}`}
                        >
                          <div className="flex items-center gap-3">
                            <div className="text-muted-foreground">
                              {getDeviceIcon(session.device_type)}
                            </div>
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="font-medium text-sm">
                                  {sessionUser?.full_name || sessionUser?.email || session.user_id}
                                </span>
                                <Badge
                                  variant={session.is_active && !isExpired ? 'default' : 'secondary'}
                                  className="text-xs"
                                >
                                  {session.is_active && !isExpired ? 'Activa' : 'Inactiva'}
                                </Badge>
                              </div>
                              <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5 flex-wrap">
                                {session.ip_address && (
                                  <span className="flex items-center gap-1 font-mono">
                                    <Globe className="h-3 w-3" />
                                    {String(session.ip_address)}
                                  </span>
                                )}
                                {session.location && (
                                  <span className="flex items-center gap-1">
                                    <MapPin className="h-3 w-3" />
                                    {session.location}
                                  </span>
                                )}
                                {session.device_type && (
                                  <span>{session.device_type}</span>
                                )}
                                {session.last_activity_at && (
                                  <span className="flex items-center gap-1">
                                    <Clock className="h-3 w-3" />
                                    {timeAgo(session.last_activity_at)}
                                  </span>
                                )}
                              </div>
                              {session.user_agent && (
                                <p className="text-xs text-muted-foreground/70 mt-0.5 truncate max-w-xs">
                                  {session.user_agent}
                                </p>
                              )}
                            </div>
                          </div>
                          {session.is_active && !isExpired && (
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 text-xs"
                              onClick={() => handleRevokeSession(session.id)}
                            >
                              <XCircle className="h-3 w-3 mr-1" />
                              Revogar
                            </Button>
                          )}
                        </div>
                      );
                    })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── TAB: AUDITORIA IAM ─────────────────────────────────────────── */}
        <TabsContent value="audit" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Registos de Auditoria IAM</CardTitle>
              <CardDescription>
                Eventos de autenticação e autorização — tabela <code className="text-xs">audit_logs</code>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Filtros */}
              <div className="flex flex-wrap gap-3">
                <div className="relative flex-1 min-w-[180px]">
                  <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input className="pl-8" placeholder="Pesquisar eventos..." value={searchAudit} onChange={e => setSearchAudit(e.target.value)} />
                </div>
                <Select value={filterAudit} onValueChange={setFilterAudit}>
                  <SelectTrigger className="w-[150px]">
                    <SelectValue placeholder="Filtrar" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos</SelectItem>
                    <SelectItem value="failure">Falhas</SelectItem>
                    <SelectItem value="success">Sucesso</SelectItem>
                    <SelectItem value="auth">Autenticação</SelectItem>
                    <SelectItem value="user">Gestão de Users</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Resumo */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: 'Total eventos', value: auditLogs.length, color: 'text-foreground' },
                  { label: 'Falhas', value: auditFailures, color: 'text-destructive' },
                  { label: 'Sucesso', value: auditLogs.length - auditFailures, color: 'text-green-600' },
                  { label: 'Taxa sucesso', value: auditLogs.length > 0 ? `${Math.round(((auditLogs.length - auditFailures) / auditLogs.length) * 100)}%` : '—', color: 'text-blue-600' },
                ].map(stat => (
                  <div key={stat.label} className="p-3 border rounded-lg">
                    <p className="text-xs text-muted-foreground">{stat.label}</p>
                    <p className={`text-xl font-bold ${stat.color}`}>{stat.value}</p>
                  </div>
                ))}
              </div>

              {loading ? (
                <div className="text-center py-10 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  A carregar registos...
                </div>
              ) : filteredAudit.length === 0 ? (
                <p className="text-center text-muted-foreground py-10">
                  {auditLogs.length === 0 ? 'Nenhum registo IAM no Supabase' : 'Sem resultados para o filtro'}
                </p>
              ) : (
                <div className="space-y-1.5">
                  {filteredAudit.map(log => {
                    const meta = log.metadata as Record<string, unknown> | null;
                    const isFailure = meta?.status === 'FAILURE' || log.action.includes('FAILED') || log.action.includes('DENIED');
                    const logUser = log.user_id ? userById(log.user_id) : null;
                    return (
                      <div
                        key={log.id}
                        className={`flex items-start gap-3 p-3 rounded-lg border transition-colors
                          ${isFailure ? 'border-destructive/20 bg-destructive/5' : 'hover:bg-muted/30'}`}
                      >
                        <div className="mt-0.5 flex-shrink-0">
                          {getActionIcon(log.action)}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <Badge variant={getActionColor(log.action)} className="text-xs font-mono">
                              {log.action}
                            </Badge>
                            <Badge variant="outline" className="text-xs">{log.resource_type}</Badge>
                            {logUser && (
                              <span className="text-xs text-muted-foreground">
                                {logUser.full_name || logUser.email}
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground flex-wrap">
                            {meta?.ip && (
                              <span className="flex items-center gap-1 font-mono">
                                <Globe className="h-3 w-3" />
                                {String(meta.ip)}
                              </span>
                            )}
                            {meta?.device && (
                              <span className="flex items-center gap-1">
                                <Monitor className="h-3 w-3" />
                                {String(meta.device)}
                              </span>
                            )}
                            {meta?.location && (
                              <span className="flex items-center gap-1">
                                <MapPin className="h-3 w-3" />
                                {String(meta.location)}
                              </span>
                            )}
                            {meta?.reason && (
                              <span className="text-destructive font-medium">{String(meta.reason)}</span>
                            )}
                          </div>
                        </div>
                        <div className="text-xs text-muted-foreground flex-shrink-0 text-right">
                          <p>{timeAgo(log.created_at)}</p>
                          {log.created_at && (
                            <p className="text-xs opacity-60">
                              {new Date(log.created_at).toLocaleTimeString('pt-AO', { hour: '2-digit', minute: '2-digit' })}
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── TAB: ALERTAS / NOTIFICAÇÕES ────────────────────────────────── */}
        <TabsContent value="notifications" className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Alertas de Segurança IAM</CardTitle>
                  <CardDescription>
                    Notificações de acesso — tabela <code className="text-xs">notifications</code>
                  </CardDescription>
                </div>
                {unreadNotifs > 0 && (
                  <Badge variant="destructive">{unreadNotifs} não lidos</Badge>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="text-center py-10 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  A carregar alertas...
                </div>
              ) : notifications.length === 0 ? (
                <p className="text-center text-muted-foreground py-10">
                  Nenhum alerta encontrado no Supabase
                </p>
              ) : (
                <div className="space-y-3">
                  {notifications.map(notif => {
                    const meta = notif.metadata as Record<string, unknown> | null;
                    const notifUser = notif.user_id ? userById(notif.user_id) : null;
                    return (
                      <div
                        key={notif.id}
                        className={`p-4 border rounded-lg space-y-2 transition-opacity ${notif.is_read ? 'opacity-60' : ''} ${getNotifTypeColor(notif.type)}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-center gap-2">
                            {notif.type.toUpperCase() === 'WARNING' ? (
                              <AlertTriangle className="h-4 w-4 text-orange-500 flex-shrink-0" />
                            ) : notif.type.toUpperCase() === 'ERROR' ? (
                              <XCircle className="h-4 w-4 text-destructive flex-shrink-0" />
                            ) : (
                              <Bell className="h-4 w-4 text-blue-500 flex-shrink-0" />
                            )}
                            <span className="font-semibold text-sm">{notif.title}</span>
                            {!notif.is_read && (
                              <div className="h-2 w-2 rounded-full bg-destructive flex-shrink-0" />
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">{timeAgo(notif.created_at)}</span>
                            {!notif.is_read && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-6 text-xs px-2"
                                onClick={() => handleMarkNotifRead(notif.id)}
                              >
                                Marcar lido
                              </Button>
                            )}
                          </div>
                        </div>
                        <p className="text-sm text-muted-foreground">{notif.message}</p>
                        <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
                          {notifUser && (
                            <span className="flex items-center gap-1">
                              <Users className="h-3 w-3" />
                              {notifUser.full_name || notifUser.email}
                            </span>
                          )}
                          {meta?.ip && (
                            <span className="flex items-center gap-1 font-mono">
                              <Globe className="h-3 w-3" />
                              {String(meta.ip)}
                            </span>
                          )}
                          {meta?.action && (
                            <Badge variant="outline" className="text-xs font-mono">
                              {String(meta.action)}
                            </Badge>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
