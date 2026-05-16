// Roles Page - Versão Completa e Funcional
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Shield, Plus, Edit, Trash2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

interface Role {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  users_count: number;
  created_at: string;
}

const ALL_PERMISSIONS = [
  'users.view', 'users.create', 'users.edit', 'users.delete',
  'finance.view', 'finance.create', 'finance.edit', 'finance.delete',
  'invoices.view', 'invoices.create', 'invoices.edit', 'invoices.delete',
  'reports.view', 'reports.create', 'reports.export',
  'hr.view', 'hr.create', 'hr.edit', 'hr.delete',
  'settings.view', 'settings.edit'
];

const generateMockRoles = (): Role[] => [
  {
    id: 'role-1',
    name: 'Administrador',
    description: 'Acesso total ao sistema',
    permissions: ALL_PERMISSIONS,
    users_count: 3,
    created_at: '2026-01-01'
  },
  {
    id: 'role-2',
    name: 'Gerente',
    description: 'Acesso de gerenciamento',
    permissions: ALL_PERMISSIONS.filter(p => !p.includes('delete') && !p.includes('settings')),
    users_count: 8,
    created_at: '2026-01-15'
  },
  {
    id: 'role-3',
    name: 'Usuário',
    description: 'Acesso básico',
    permissions: ALL_PERMISSIONS.filter(p => p.includes('view')),
    users_count: 15,
    created_at: '2026-02-01'
  },
  {
    id: 'role-4',
    name: 'Visualizador',
    description: 'Apenas visualização',
    permissions: ['users.view', 'finance.view', 'reports.view'],
    users_count: 5,
    created_at: '2026-02-15'
  }
];

export default function Roles() {
  const [roles, setRoles] = useState<Role[]>(generateMockRoles());
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingRole, setEditingRole] = useState<Role | null>(null);

  const [formData, setFormData] = useState({
    name: '',
    description: '',
    permissions: [] as string[]
  });

  const handleCreateRole = () => {
    if (!formData.name) {
      toast.error('Preencha o nome da função');
      return;
    }

    const newRole: Role = {
      id: `role-${Date.now()}`,
      name: formData.name,
      description: formData.description,
      permissions: formData.permissions,
      users_count: 0,
      created_at: new Date().toISOString().split('T')[0]
    };

    setRoles([...roles, newRole]);
    setIsDialogOpen(false);
    resetForm();
    toast.success('Função criada com sucesso!');
  };

  const handleUpdateRole = () => {
    if (!editingRole) return;

    const updated = roles.map(r =>
      r.id === editingRole.id ? { ...r, ...formData } : r
    );

    setRoles(updated);
    setIsDialogOpen(false);
    resetForm();
    toast.success('Função atualizada!');
  };

  const handleDeleteRole = (id: string) => {
    if (!confirm('Tem certeza que deseja remover esta função?')) return;
    setRoles(roles.filter(r => r.id !== id));
    toast.success('Função removida!');
  };

  const handleEditRole = (role: Role) => {
    setEditingRole(role);
    setFormData({
      name: role.name,
      description: role.description,
      permissions: role.permissions
    });
    setIsDialogOpen(true);
  };

  const togglePermission = (permission: string) => {
    setFormData(prev => ({
      ...prev,
      permissions: prev.permissions.includes(permission)
        ? prev.permissions.filter(p => p !== permission)
        : [...prev.permissions, permission]
    }));
  };

  const resetForm = () => {
    setFormData({ name: '', description: '', permissions: [] });
    setEditingRole(null);
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Funções e Permissões</h1>
            <p className="text-muted-foreground">Gestão de funções do sistema</p>
          </div>
          <div className="flex gap-3">
            <Button variant="outline" onClick={() => setRoles(generateMockRoles())}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Atualizar
            </Button>
            <Dialog open={isDialogOpen} onOpenChange={(open) => {
              setIsDialogOpen(open);
              if (!open) resetForm();
            }}>
              <DialogTrigger asChild>
                <Button>
                  <Plus className="h-4 w-4 mr-2" />
                  Nova Função
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                  <DialogTitle>{editingRole ? 'Editar' : 'Nova'} Função</DialogTitle>
                  <DialogDescription>Configure a função e suas permissões</DialogDescription>
                </DialogHeader>
                <div className="grid gap-4 py-4">
                  <div className="space-y-2">
                    <Label>Nome da Função *</Label>
                    <Input
                      value={formData.name}
                      onChange={(e) => setFormData({...formData, name: e.target.value})}
                      placeholder="Ex: Gerente Financeiro"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Descrição</Label>
                    <Input
                      value={formData.description}
                      onChange={(e) => setFormData({...formData, description: e.target.value})}
                      placeholder="Descrição da função"
                    />
                  </div>
                  <div className="space-y-4">
                    <Label>Permissões ({formData.permissions.length}/{ALL_PERMISSIONS.length})</Label>
                    <div className="border rounded-lg p-4 max-h-96 overflow-y-auto space-y-3">
                      {['users', 'finance', 'invoices', 'reports', 'hr', 'settings'].map(module => (
                        <div key={module} className="space-y-2">
                          <p className="font-medium capitalize">{module}</p>
                          <div className="grid grid-cols-2 gap-2 pl-4">
                            {ALL_PERMISSIONS.filter(p => p.startsWith(module)).map(permission => (
                              <div key={permission} className="flex items-center space-x-2">
                                <Checkbox
                                  checked={formData.permissions.includes(permission)}
                                  onCheckedChange={() => togglePermission(permission)}
                                />
                                <label className="text-sm">{permission.split('.')[1]}</label>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="flex justify-end gap-3">
                  <Button variant="outline" onClick={() => {
                    setIsDialogOpen(false);
                    resetForm();
                  }}>
                    Cancelar
                  </Button>
                  <Button onClick={editingRole ? handleUpdateRole : handleCreateRole}>
                    {editingRole ? 'Atualizar' : 'Criar'}
                  </Button>
                </div>
              </DialogContent>
            </Dialog>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Funções</CardTitle>
              <Shield className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{roles.length}</div>
              <p className="text-xs text-muted-foreground">funções ativas</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Usuários</CardTitle>
              <Shield className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {roles.reduce((sum, r) => sum + r.users_count, 0)}
              </div>
              <p className="text-xs text-muted-foreground">usuários atribuídos</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Permissões</CardTitle>
              <Shield className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{ALL_PERMISSIONS.length}</div>
              <p className="text-xs text-muted-foreground">permissões disponíveis</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Média de Permissões</CardTitle>
              <Shield className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {Math.round(roles.reduce((sum, r) => sum + r.permissions.length, 0) / roles.length)}
              </div>
              <p className="text-xs text-muted-foreground">por função</p>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Funções ({roles.length})</CardTitle>
            <CardDescription>Lista de todas as funções</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {roles.map((role) => (
                <div key={role.id} className="flex items-center justify-between border-b pb-4 last:border-0">
                  <div className="flex-1">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                        <Shield className="h-5 w-5 text-primary" />
                      </div>
                      <div>
                        <p className="font-medium">{role.name}</p>
                        <p className="text-sm text-muted-foreground">{role.description}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <Badge variant="outline">
                            {role.permissions.length} permissões
                          </Badge>
                          <Badge variant="secondary">
                            {role.users_count} usuários
                          </Badge>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button variant="ghost" size="sm" onClick={() => handleEditRole(role)}>
                      <Edit className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDeleteRole(role.id)}>
                      <Trash2 className="h-4 w-4 text-red-600" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}
