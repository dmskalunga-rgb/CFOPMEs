import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Shield, Key, CheckCircle, XCircle, RefreshCw, Plus, Trash2,
  Lock, Users, Settings, Database, ChevronDown, ChevronUp, Search
} from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { supabase } from '@/integrations/supabase/client';

// ─── Interfaces baseadas nas colunas reais das tabelas ───────────────────────

interface RoleRow {
  id: string;
  name: string;
  display_name: string | null;
  description: string | null;
  is_system: boolean | null;
  tenant_id: string | null;
  created_at: string | null;
}

interface PermissionRow {
  id: string;
  name: string;
  display_name: string | null;
  resource: string;
  action: string;
  category: string | null;
}

interface RolePermissionRow {
  id: string;
  role_id: string;
  permission_id: string;
  granted_at: string | null;
  permission: PermissionRow | null;
}

interface UserRow {
  id: string;
  full_name: string | null;
  email: string | null;
  role: string | null;
  department: string | null;
  status: string | null;
}

interface UserRoleRow {
  id: string;
  user_id: string;
  tenant_id: string;
  is_active: boolean | null;
  assigned_at: string | null;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

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

function getCategoryIcon(category: string | null) {
  switch (category) {
    case 'Faturação': return <Database className="h-3 w-3" />;
    case 'RH':        return <Users className="h-3 w-3" />;
    case 'Folha':     return <Key className="h-3 w-3" />;
    case 'Finanças':  return <Lock className="h-3 w-3" />;
    case 'Gestão':    return <Settings className="h-3 w-3" />;
    default:          return <Shield className="h-3 w-3" />;
  }
}

function getCategoryColor(category: string | null): string {
  switch (category) {
    case 'Faturação':  return 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200';
    case 'RH':         return 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200';
    case 'Folha':      return 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200';
    case 'Finanças':   return 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200';
    case 'Gestão':     return 'bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-200';
    case 'Relatórios': return 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900 dark:text-cyan-200';
    case 'Sistema':    return 'bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200';
    default:           return 'bg-muted text-muted-foreground';
  }
}

// ─── Componente principal ─────────────────────────────────────────────────────

export default function RBACDashboard() {
  const { toast } = useToast();

  const [loading, setLoading]           = useState(false);
  const [tenantId, setTenantId]         = useState<string | null>(null);

  // Dados
  const [roles, setRoles]               = useState<RoleRow[]>([]);
  const [permissions, setPermissions]   = useState<PermissionRow[]>([]);
  const [rolePermissions, setRolePerms] = useState<RolePermissionRow[]>([]);
  const [users, setUsers]               = useState<UserRow[]>([]);
  const [userRoles, setUserRoles]       = useState<UserRoleRow[]>([]);

  // UI state
  const [selectedRole, setSelectedRole]     = useState<RoleRow | null>(null);
  const [expandedRoles, setExpandedRoles]   = useState<Set<string>>(new Set());
  const [searchPerm, setSearchPerm]         = useState('');
  const [searchRole, setSearchRole]         = useState('');

  // Formulário nova role
  const [showCreateRole, setShowCreateRole]     = useState(false);
  const [newRoleName, setNewRoleName]           = useState('');
  const [newRoleDisplay, setNewRoleDisplay]     = useState('');
  const [newRoleDesc, setNewRoleDesc]           = useState('');
  const [creatingRole, setCreatingRole]         = useState(false);

  // Simulador de acesso
  const [simResource, setSimResource]           = useState('invoices');
  const [simAction, setSimAction]               = useState('view');
  const [simUserId, setSimUserId]               = useState('');
  const [simResult, setSimResult]               = useState<{ allowed: boolean; reason: string; matchedPerms: string[] } | null>(null);
  const [simLoading, setSimLoading]             = useState(false);

  // ─── Inicialização ────────────────────────────────────────────────────────

  useEffect(() => {
    getTenantId().then(setTenantId);
  }, []);

  useEffect(() => {
    if (tenantId) loadData(tenantId);
  }, [tenantId]);

  const loadData = async (tid: string) => {
    setLoading(true);
    try {
      const [rolesRes, permsRes, rolePermsRes, usersRes, userRolesRes] = await Promise.all([
        // Roles: do tenant ou globais
        supabase
          .from('roles')
          .select('id, name, display_name, description, is_system, tenant_id, created_at')
          .or(`tenant_id.eq.${tid},tenant_id.is.null`)
          .order('name'),

        // Todas as permissões
        supabase
          .from('permissions')
          .select('id, name, display_name, resource, action, category')
          .order('category, name'),

        // Role-permissions com detalhes da permissão
        supabase
          .from('role_permissions')
          .select(`
            id, role_id, permission_id, granted_at,
            permission:permissions(id, name, display_name, resource, action, category)
          `)
          .order('granted_at', { ascending: false }),

        // Utilizadores do tenant
        supabase
          .from('users')
          .select('id, full_name, email, role, department, status')
          .eq('tenant_id', tid)
          .order('full_name'),

        // User roles (sem role_id — estrutura real)
        supabase
          .from('user_roles')
          .select('id, user_id, tenant_id, is_active, assigned_at')
          .eq('tenant_id', tid)
          .order('assigned_at', { ascending: false })
          .limit(100),
      ]);

      if (rolesRes.error)    console.error('roles error:', rolesRes.error);
      if (permsRes.error)    console.error('permissions error:', permsRes.error);
      if (rolePermsRes.error) console.error('role_permissions error:', rolePermsRes.error);
      if (usersRes.error)    console.error('users error:', usersRes.error);
      if (userRolesRes.error) console.error('user_roles error:', userRolesRes.error);

      setRoles((rolesRes.data as RoleRow[]) ?? []);
      setPermissions((permsRes.data as PermissionRow[]) ?? []);
      setRolePerms((rolePermsRes.data as unknown as RolePermissionRow[]) ?? []);
      setUsers((usersRes.data as UserRow[]) ?? []);
      setUserRoles((userRolesRes.data as UserRoleRow[]) ?? []);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro desconhecido';
      toast({ title: 'Erro ao carregar dados RBAC', description: msg, variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  // ─── Acções ───────────────────────────────────────────────────────────────

  const handleCreateRole = async () => {
    if (!newRoleName.trim() || !tenantId) return;
    setCreatingRole(true);
    try {
      const { error } = await supabase
        .from('roles')
        .insert({
          tenant_id:    tenantId,
          name:         newRoleName.trim().toUpperCase().replace(/\s+/g, '_'),
          display_name: newRoleDisplay.trim() || newRoleName.trim(),
          description:  newRoleDesc.trim() || null,
          is_system:    false,
        });
      if (error) throw error;
      toast({ title: 'Role criada com sucesso' });
      setShowCreateRole(false);
      setNewRoleName('');
      setNewRoleDisplay('');
      setNewRoleDesc('');
      if (tenantId) loadData(tenantId);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao criar role';
      toast({ title: 'Erro ao criar role', description: msg, variant: 'destructive' });
    } finally {
      setCreatingRole(false);
    }
  };

  const handleDeleteRole = async (roleId: string, isSystem: boolean | null) => {
    if (isSystem) {
      toast({ title: 'Não é possível eliminar roles de sistema', variant: 'destructive' });
      return;
    }
    try {
      const { error } = await supabase.from('roles').delete().eq('id', roleId);
      if (error) throw error;
      toast({ title: 'Role eliminada com sucesso' });
      if (selectedRole?.id === roleId) setSelectedRole(null);
      if (tenantId) loadData(tenantId);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao eliminar role';
      toast({ title: 'Erro ao eliminar role', description: msg, variant: 'destructive' });
    }
  };

  const handleGrantPermission = async (roleId: string, permissionId: string) => {
    try {
      const { error } = await supabase
        .from('role_permissions')
        .insert({ role_id: roleId, permission_id: permissionId });
      if (error) throw error;
      toast({ title: 'Permissão concedida' });
      if (tenantId) loadData(tenantId);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro';
      toast({ title: 'Erro ao conceder permissão', description: msg, variant: 'destructive' });
    }
  };

  const handleRevokePermission = async (rolePermId: string) => {
    try {
      const { error } = await supabase.from('role_permissions').delete().eq('id', rolePermId);
      if (error) throw error;
      toast({ title: 'Permissão revogada' });
      if (tenantId) loadData(tenantId);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro';
      toast({ title: 'Erro ao revogar permissão', description: msg, variant: 'destructive' });
    }
  };

  // Simulador de acesso: verifica se o utilizador (pelo role na tabela users) tem a permissão
  const handleSimulateAccess = async () => {
    if (!simUserId) {
      toast({ title: 'Selecione um utilizador', variant: 'destructive' });
      return;
    }
    setSimLoading(true);
    setSimResult(null);
    try {
      // Obter role do utilizador
      const user = users.find(u => u.id === simUserId);
      if (!user?.role) {
        setSimResult({ allowed: false, reason: 'Utilizador sem role atribuído', matchedPerms: [] });
        return;
      }

      // Obter role da tabela roles
      const roleRow = roles.find(r => r.name === user.role);
      if (!roleRow) {
        setSimResult({ allowed: false, reason: `Role "${user.role}" não encontrado na tabela roles`, matchedPerms: [] });
        return;
      }

      // Obter permissões da role
      const rolePermsForRole = rolePermissions.filter(rp => rp.role_id === roleRow.id);
      const permNames = rolePermsForRole
        .filter(rp => rp.permission !== null)
        .map(rp => rp.permission!.name);

      const permissionName = `${simResource}.${simAction}`;
      const hasPermission  = permNames.includes(permissionName);

      setSimResult({
        allowed:      hasPermission,
        reason:       hasPermission
          ? `Role "${roleRow.display_name || roleRow.name}" tem a permissão "${permissionName}"`
          : `Role "${roleRow.display_name || roleRow.name}" NÃO tem a permissão "${permissionName}"`,
        matchedPerms: permNames,
      });
    } finally {
      setSimLoading(false);
    }
  };

  // ─── Dados computados ─────────────────────────────────────────────────────

  const filteredRoles = roles.filter(r =>
    r.name.toLowerCase().includes(searchRole.toLowerCase()) ||
    (r.display_name ?? '').toLowerCase().includes(searchRole.toLowerCase())
  );

  const filteredPermissions = permissions.filter(p =>
    p.name.toLowerCase().includes(searchPerm.toLowerCase()) ||
    (p.display_name ?? '').toLowerCase().includes(searchPerm.toLowerCase()) ||
    (p.category ?? '').toLowerCase().includes(searchPerm.toLowerCase())
  );

  const getPermissionsForRole = (roleId: string): RolePermissionRow[] =>
    rolePermissions.filter(rp => rp.role_id === roleId);

  const getPermissionIdsForRole = (roleId: string): Set<string> =>
    new Set(rolePermissions.filter(rp => rp.role_id === roleId).map(rp => rp.permission_id));

  const getCategoriesForRole = (roleId: string): Record<string, PermissionRow[]> => {
    const permsForRole = getPermissionsForRole(roleId);
    const result: Record<string, PermissionRow[]> = {};
    permsForRole.forEach(rp => {
      if (rp.permission) {
        const cat = rp.permission.category ?? 'Outros';
        if (!result[cat]) result[cat] = [];
        result[cat].push(rp.permission);
      }
    });
    return result;
  };

  const totalAssignments = userRoles.filter(ur => ur.is_active).length;

  // Categorias únicas
  const categories = Array.from(new Set(permissions.map(p => p.category ?? 'Outros'))).sort();

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="container mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Shield className="h-8 w-8 text-primary" />
            RBAC Dashboard
          </h1>
          <p className="text-muted-foreground mt-1">
            Controlo de Acesso Baseado em Funções (Role-Based Access Control)
          </p>
        </div>
        <Button onClick={() => tenantId && loadData(tenantId)} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Atualizar
        </Button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Roles</CardTitle>
            <Shield className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{roles.length}</div>
            <p className="text-xs text-muted-foreground">
              {roles.filter(r => r.is_system).length} de sistema
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Permissões</CardTitle>
            <Key className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{permissions.length}</div>
            <p className="text-xs text-muted-foreground">
              {categories.length} categorias
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Atribuições</CardTitle>
            <Users className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{rolePermissions.length}</div>
            <p className="text-xs text-muted-foreground">
              Role-permissão mapeadas
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Utilizadores com Role</CardTitle>
            <Lock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {users.filter(u => u.role).length}
            </div>
            <p className="text-xs text-muted-foreground">
              {totalAssignments} atribuições activas (user_roles)
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Tabs principais */}
      <Tabs defaultValue="roles" className="space-y-4">
        <TabsList>
          <TabsTrigger value="roles">Roles</TabsTrigger>
          <TabsTrigger value="permissions">Permissões</TabsTrigger>
          <TabsTrigger value="matrix">Matriz Roles × Permissões</TabsTrigger>
          <TabsTrigger value="users">Utilizadores & Roles</TabsTrigger>
          <TabsTrigger value="simulator">Simulador de Acesso</TabsTrigger>
        </TabsList>

        {/* ── TAB: ROLES ─────────────────────────────────────────────────── */}
        <TabsContent value="roles" className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Roles do Sistema</CardTitle>
                  <CardDescription>
                    Funções com permissões associadas — dados da tabela <code className="text-xs">roles</code>
                  </CardDescription>
                </div>
                <Button onClick={() => setShowCreateRole(!showCreateRole)}>
                  <Plus className="h-4 w-4 mr-2" />
                  Nova Role
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Formulário nova role */}
              {showCreateRole && (
                <div className="border rounded-lg p-4 bg-muted/30 space-y-3">
                  <h3 className="font-semibold text-sm">Criar Nova Role</h3>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <Label className="text-xs">Nome (identificador)</Label>
                      <Input
                        placeholder="FINANCE_MANAGER"
                        value={newRoleName}
                        onChange={e => setNewRoleName(e.target.value)}
                        className="mt-1"
                      />
                    </div>
                    <div>
                      <Label className="text-xs">Nome de Exibição</Label>
                      <Input
                        placeholder="Gestor Financeiro"
                        value={newRoleDisplay}
                        onChange={e => setNewRoleDisplay(e.target.value)}
                        className="mt-1"
                      />
                    </div>
                  </div>
                  <div>
                    <Label className="text-xs">Descrição</Label>
                    <Input
                      placeholder="Descrição da role..."
                      value={newRoleDesc}
                      onChange={e => setNewRoleDesc(e.target.value)}
                      className="mt-1"
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" onClick={handleCreateRole} disabled={creatingRole || !newRoleName.trim()}>
                      {creatingRole ? <RefreshCw className="h-3 w-3 animate-spin mr-1" /> : null}
                      Criar Role
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setShowCreateRole(false)}>
                      Cancelar
                    </Button>
                  </div>
                </div>
              )}

              {/* Pesquisa */}
              <div className="relative">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  className="pl-8"
                  placeholder="Pesquisar roles..."
                  value={searchRole}
                  onChange={e => setSearchRole(e.target.value)}
                />
              </div>

              {/* Lista de roles */}
              {loading ? (
                <div className="text-center py-8 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  Carregando roles...
                </div>
              ) : filteredRoles.length === 0 ? (
                <p className="text-center text-muted-foreground py-8">
                  {roles.length === 0 ? 'Nenhuma role encontrada no Supabase' : 'Sem resultados para a pesquisa'}
                </p>
              ) : (
                <div className="space-y-2">
                  {filteredRoles.map(role => {
                    const expanded    = expandedRoles.has(role.id);
                    const categories  = getCategoriesForRole(role.id);
                    const permCount   = getPermissionsForRole(role.id).length;
                    return (
                      <div key={role.id} className="border rounded-lg overflow-hidden">
                        <div
                          className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                          onClick={() => {
                            const next = new Set(expandedRoles);
                            expanded ? next.delete(role.id) : next.add(role.id);
                            setExpandedRoles(next);
                            setSelectedRole(role);
                          }}
                        >
                          <div className="flex items-center gap-3">
                            <Shield className="h-5 w-5 text-primary" />
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="font-semibold">{role.display_name || role.name}</span>
                                {role.is_system && (
                                  <Badge variant="secondary" className="text-xs">Sistema</Badge>
                                )}
                                <Badge variant="outline" className="text-xs font-mono">
                                  {role.name}
                                </Badge>
                              </div>
                              {role.description && (
                                <p className="text-xs text-muted-foreground mt-0.5">{role.description}</p>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="text-sm text-muted-foreground">
                              {permCount} permissão{permCount !== 1 ? 'ões' : ''}
                            </span>
                            {!role.is_system && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={e => { e.stopPropagation(); handleDeleteRole(role.id, role.is_system); }}
                              >
                                <Trash2 className="h-4 w-4 text-destructive" />
                              </Button>
                            )}
                            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                          </div>
                        </div>

                        {expanded && (
                          <div className="border-t bg-muted/10 p-4 space-y-3">
                            {permCount === 0 ? (
                              <p className="text-sm text-muted-foreground">Nenhuma permissão atribuída a esta role.</p>
                            ) : (
                              Object.entries(categories).sort().map(([cat, perms]) => (
                                <div key={cat}>
                                  <div className="flex items-center gap-1.5 mb-2">
                                    {getCategoryIcon(cat)}
                                    <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                                      {cat}
                                    </span>
                                  </div>
                                  <div className="flex flex-wrap gap-1.5">
                                    {perms.map(p => {
                                      const rp = rolePermissions.find(
                                        x => x.role_id === role.id && x.permission_id === p.id
                                      );
                                      return (
                                        <div
                                          key={p.id}
                                          className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-xs ${getCategoryColor(cat)}`}
                                        >
                                          <CheckCircle className="h-3 w-3" />
                                          <span>{p.display_name || p.name}</span>
                                          {rp && (
                                            <button
                                              className="ml-1 hover:opacity-70"
                                              onClick={() => handleRevokePermission(rp.id)}
                                              title="Revogar"
                                            >
                                              <XCircle className="h-3 w-3" />
                                            </button>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              ))
                            )}

                            {/* Adicionar permissão */}
                            <div className="pt-2 border-t">
                              <p className="text-xs text-muted-foreground mb-2">Adicionar permissão:</p>
                              <div className="flex flex-wrap gap-1.5">
                                {permissions
                                  .filter(p => !getPermissionIdsForRole(role.id).has(p.id))
                                  .map(p => (
                                    <button
                                      key={p.id}
                                      onClick={() => handleGrantPermission(role.id, p.id)}
                                      className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs border border-dashed hover:bg-muted transition-colors"
                                    >
                                      <Plus className="h-3 w-3" />
                                      {p.display_name || p.name}
                                    </button>
                                  ))}
                                {permissions.filter(p => !getPermissionIdsForRole(role.id).has(p.id)).length === 0 && (
                                  <span className="text-xs text-muted-foreground italic">
                                    Todas as permissões já foram concedidas
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── TAB: PERMISSÕES ───────────────────────────────────────────── */}
        <TabsContent value="permissions" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Catálogo de Permissões</CardTitle>
              <CardDescription>
                Todas as permissões disponíveis — tabela <code className="text-xs">permissions</code>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="relative">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  className="pl-8"
                  placeholder="Pesquisar permissões..."
                  value={searchPerm}
                  onChange={e => setSearchPerm(e.target.value)}
                />
              </div>

              {loading ? (
                <div className="text-center py-8 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  Carregando permissões...
                </div>
              ) : (
                <div className="space-y-4">
                  {categories.map(cat => {
                    const catPerms = filteredPermissions.filter(p => (p.category ?? 'Outros') === cat);
                    if (catPerms.length === 0) return null;
                    return (
                      <div key={cat}>
                        <div className="flex items-center gap-2 mb-2">
                          {getCategoryIcon(cat)}
                          <h3 className="font-semibold text-sm">{cat}</h3>
                          <Badge variant="secondary" className="text-xs">{catPerms.length}</Badge>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
                          {catPerms.map(p => (
                            <div key={p.id} className="p-3 border rounded-lg flex items-start gap-2">
                              <div className={`p-1 rounded ${getCategoryColor(cat)}`}>
                                {getCategoryIcon(cat)}
                              </div>
                              <div className="flex-1 min-w-0">
                                <p className="font-medium text-sm">{p.display_name || p.name}</p>
                                <p className="text-xs text-muted-foreground font-mono mt-0.5">{p.name}</p>
                                <div className="flex items-center gap-1 mt-1">
                                  <Badge variant="outline" className="text-xs">{p.resource}</Badge>
                                  <Badge variant="outline" className="text-xs">{p.action}</Badge>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                  {filteredPermissions.length === 0 && (
                    <p className="text-center text-muted-foreground py-8">
                      {permissions.length === 0 ? 'Nenhuma permissão encontrada no Supabase' : 'Sem resultados'}
                    </p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── TAB: MATRIZ ───────────────────────────────────────────────── */}
        <TabsContent value="matrix" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Matriz Roles × Permissões</CardTitle>
              <CardDescription>
                Visão consolidada de qual role tem qual permissão
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="text-center py-8 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  Carregando matriz...
                </div>
              ) : roles.length === 0 || permissions.length === 0 ? (
                <p className="text-center text-muted-foreground py-8">Sem dados suficientes para exibir a matriz</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs border-collapse">
                    <thead>
                      <tr>
                        <th className="p-2 text-left border bg-muted font-semibold min-w-[180px]">Permissão</th>
                        {roles.map(r => (
                          <th key={r.id} className="p-2 text-center border bg-muted font-semibold whitespace-nowrap">
                            <span className="writing-mode-vertical">{r.display_name || r.name}</span>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {categories.map(cat => {
                        const catPerms = permissions.filter(p => (p.category ?? 'Outros') === cat);
                        return catPerms.map((perm, idx) => (
                          <tr key={perm.id} className={idx % 2 === 0 ? 'bg-background' : 'bg-muted/20'}>
                            <td className="p-2 border">
                              {idx === 0 && (
                                <span className={`inline-block px-1.5 py-0.5 rounded text-xs mb-1 ${getCategoryColor(cat)}`}>
                                  {cat}
                                </span>
                              )}
                              <div className="flex flex-col">
                                <span className="font-medium">{perm.display_name || perm.name}</span>
                                <span className="font-mono text-muted-foreground">{perm.name}</span>
                              </div>
                            </td>
                            {roles.map(role => {
                              const has = getPermissionIdsForRole(role.id).has(perm.id);
                              return (
                                <td key={role.id} className="p-2 border text-center">
                                  {has ? (
                                    <CheckCircle className="h-4 w-4 text-green-600 mx-auto" />
                                  ) : (
                                    <XCircle className="h-4 w-4 text-muted-foreground/30 mx-auto" />
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        ));
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── TAB: UTILIZADORES & ROLES ─────────────────────────────────── */}
        <TabsContent value="users" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Utilizadores e Roles Atribuídos</CardTitle>
              <CardDescription>
                Roles actuais dos utilizadores — coluna <code className="text-xs">role</code> na tabela <code className="text-xs">users</code>
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="text-center py-8 text-muted-foreground">
                  <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-2" />
                  Carregando utilizadores...
                </div>
              ) : users.length === 0 ? (
                <p className="text-center text-muted-foreground py-8">Nenhum utilizador encontrado</p>
              ) : (
                <div className="space-y-2">
                  {users.map(u => {
                    const roleRow = roles.find(r => r.name === u.role);
                    const permCount = roleRow ? getPermissionsForRole(roleRow.id).length : 0;
                    return (
                      <div key={u.id} className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/30 transition-colors">
                        <div className="flex items-center gap-3">
                          <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center">
                            <span className="text-sm font-semibold text-primary">
                              {(u.full_name || u.email || '?').charAt(0).toUpperCase()}
                            </span>
                          </div>
                          <div>
                            <p className="font-medium text-sm">{u.full_name || '(sem nome)'}</p>
                            <p className="text-xs text-muted-foreground">{u.email || '—'}</p>
                            {u.department && (
                              <p className="text-xs text-muted-foreground">{u.department}</p>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-3 text-right">
                          {u.role ? (
                            <div>
                              <Badge variant="default" className="mb-1">
                                {roleRow?.display_name || u.role}
                              </Badge>
                              <p className="text-xs text-muted-foreground">{permCount} permissões</p>
                            </div>
                          ) : (
                            <Badge variant="secondary">Sem role</Badge>
                          )}
                          <Badge
                            variant={u.status === 'active' ? 'outline' : 'destructive'}
                            className="text-xs"
                          >
                            {u.status || 'desconhecido'}
                          </Badge>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          {/* user_roles table entries */}
          {userRoles.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Entradas na Tabela user_roles</CardTitle>
                <CardDescription>
                  Registos directos da tabela <code className="text-xs">user_roles</code> do tenant
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {userRoles.map(ur => {
                    const user = users.find(u => u.id === ur.user_id);
                    return (
                      <div key={ur.id} className="flex items-center justify-between p-3 border rounded-lg">
                        <div>
                          <p className="font-medium text-sm">{user?.full_name || user?.email || ur.user_id}</p>
                          <p className="text-xs text-muted-foreground font-mono">ID: {ur.user_id}</p>
                          {ur.assigned_at && (
                            <p className="text-xs text-muted-foreground">
                              Atribuído: {new Date(ur.assigned_at).toLocaleString('pt-AO')}
                            </p>
                          )}
                        </div>
                        <Badge variant={ur.is_active ? 'default' : 'secondary'}>
                          {ur.is_active ? 'Activo' : 'Inactivo'}
                        </Badge>
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ── TAB: SIMULADOR ────────────────────────────────────────────── */}
        <TabsContent value="simulator" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Simulador de Controlo de Acesso</CardTitle>
              <CardDescription>
                Verifica se um utilizador tem permissão para um recurso/acção com base no seu role
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <Label className="text-sm">Utilizador</Label>
                  <Select value={simUserId} onValueChange={setSimUserId}>
                    <SelectTrigger className="mt-1">
                      <SelectValue placeholder="Selecione um utilizador" />
                    </SelectTrigger>
                    <SelectContent>
                      {users.map(u => (
                        <SelectItem key={u.id} value={u.id}>
                          {u.full_name || u.email || u.id} {u.role ? `(${u.role})` : '(sem role)'}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label className="text-sm">Recurso</Label>
                  <Select value={simResource} onValueChange={setSimResource}>
                    <SelectTrigger className="mt-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Array.from(new Set(permissions.map(p => p.resource))).sort().map(r => (
                        <SelectItem key={r} value={r}>{r}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <Label className="text-sm">Acção</Label>
                  <Select value={simAction} onValueChange={setSimAction}>
                    <SelectTrigger className="mt-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Array.from(new Set(
                        permissions.filter(p => p.resource === simResource).map(p => p.action)
                      )).sort().map(a => (
                        <SelectItem key={a} value={a}>{a}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <Button onClick={handleSimulateAccess} disabled={simLoading || !simUserId} className="w-full">
                {simLoading
                  ? <><RefreshCw className="h-4 w-4 mr-2 animate-spin" /> A verificar...</>
                  : <><Key className="h-4 w-4 mr-2" /> Verificar Acesso</>
                }
              </Button>

              {simResult && (
                <div className={`p-4 rounded-lg border ${simResult.allowed
                  ? 'border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950'
                  : 'border-destructive/30 bg-destructive/10'
                }`}>
                  <div className="flex items-center gap-2 mb-2">
                    {simResult.allowed
                      ? <CheckCircle className="h-5 w-5 text-green-600" />
                      : <XCircle className="h-5 w-5 text-destructive" />
                    }
                    <span className={`font-semibold ${simResult.allowed ? 'text-green-700 dark:text-green-400' : 'text-destructive'}`}>
                      {simResult.allowed ? 'ACESSO PERMITIDO' : 'ACESSO NEGADO'}
                    </span>
                  </div>
                  <p className="text-sm mb-3">{simResult.reason}</p>

                  {simResult.matchedPerms.length > 0 && (
                    <div>
                      <p className="text-xs font-semibold text-muted-foreground mb-1">
                        Permissões da role ({simResult.matchedPerms.length}):
                      </p>
                      <div className="flex flex-wrap gap-1">
                        {simResult.matchedPerms.map(pm => (
                          <Badge
                            key={pm}
                            variant={pm === `${simResource}.${simAction}` ? 'default' : 'outline'}
                            className="text-xs font-mono"
                          >
                            {pm}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
