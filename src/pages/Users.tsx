import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { usersService, type User, type UserRole } from '@/services/usersServiceReal'
import { useAuth } from '@/hooks/useAuth'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Users as UsersIcon, UserPlus, Edit, Trash2, Search, Shield, Loader2 } from 'lucide-react'
import { toast } from 'sonner'

export default function Users() {
  const { profile } = useAuth()
  const queryClient = useQueryClient()
  const [isInviteDialogOpen, setIsInviteDialogOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [deleteUserId, setDeleteUserId] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [filterRole, setFilterRole] = useState<string>('all')

  const [inviteFormData, setInviteFormData] = useState({
    email: '',
    role: 'employee' as UserRole
  })

  const [editFormData, setEditFormData] = useState<UserRole>('employee')

  const { data: users = [], isLoading, error } = useQuery({
    queryKey: ['users', profile?.tenant_id],
    queryFn: async () => {
      if (!profile?.tenant_id) throw new Error('Tenant ID não encontrado')
      return usersService.getUsersByTenant(profile.tenant_id)
    },
    enabled: !!profile?.tenant_id
  })

  const inviteMutation = useMutation({
    mutationFn: async (data: { email: string; role: UserRole }) => {
      if (!profile?.tenant_id || !profile?.id) throw new Error('Dados do usuário não encontrados')
      return usersService.inviteUser({
        email: data.email,
        role: data.role,
        tenantId: profile.tenant_id,
        invitedBy: profile.id
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setIsInviteDialogOpen(false)
      resetInviteForm()
      toast.success('Convite enviado com sucesso!')
    },
    onError: (error: Error) => {
      toast.error(`Erro ao enviar convite: ${error.message}`)
    }
  })

  const updateRoleMutation = useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: UserRole }) => {
      return usersService.updateUserRole(userId, role)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setIsEditDialogOpen(false)
      setEditingUser(null)
      toast.success('Função atualizada com sucesso!')
    },
    onError: (error: Error) => {
      toast.error(`Erro ao atualizar função: ${error.message}`)
    }
  })

  const toggleStatusMutation = useMutation({
    mutationFn: async ({ userId, isActive }: { userId: string; isActive: boolean }) => {
      return isActive ? usersService.activateUser(userId) : usersService.deactivateUser(userId)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      toast.success('Status atualizado com sucesso!')
    },
    onError: (error: Error) => {
      toast.error(`Erro ao atualizar status: ${error.message}`)
    }
  })

  const deleteMutation = useMutation({
    mutationFn: async (userId: string) => {
      return usersService.deleteUser(userId)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setDeleteUserId(null)
      toast.success('Usuário removido com sucesso!')
    },
    onError: (error: Error) => {
      toast.error(`Erro ao remover usuário: ${error.message}`)
    }
  })

  const filteredUsers = users.filter(u => {
    const matchesSearch = u.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         u.email.toLowerCase().includes(searchTerm.toLowerCase())
    const matchesRole = filterRole === 'all' || u.role === filterRole
    return matchesSearch && matchesRole
  })

  const handleInviteUser = () => {
    if (!inviteFormData.email) {
      toast.error('Preencha o email')
      return
    }
    inviteMutation.mutate(inviteFormData)
  }

  const handleUpdateRole = () => {
    if (!editingUser) return
    updateRoleMutation.mutate({ userId: editingUser.id, role: editFormData })
  }

  const handleToggleStatus = (user: User) => {
    toggleStatusMutation.mutate({ userId: user.id, isActive: !user.is_active })
  }

  const handleDeleteUser = () => {
    if (!deleteUserId) return
    deleteMutation.mutate(deleteUserId)
  }

  const handleEditUser = (user: User) => {
    setEditingUser(user)
    setEditFormData(user.role)
    setIsEditDialogOpen(true)
  }

  const resetInviteForm = () => {
    setInviteFormData({ email: '', role: 'employee' })
  }

  const getRoleBadge = (role: UserRole) => {
    const variants = {
      admin: { label: 'Administrador', variant: 'destructive' as const },
      manager: { label: 'Gerente', variant: 'default' as const },
      accountant: { label: 'Contabilista', variant: 'secondary' as const },
      employee: { label: 'Funcionário', variant: 'outline' as const },
      viewer: { label: 'Visualizador', variant: 'outline' as const }
    }
    return variants[role]
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>Erro ao carregar usuários</CardTitle>
            <CardDescription>{(error as Error).message}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Usuários</h1>
          <p className="text-muted-foreground">Gestão de usuários do sistema</p>
        </div>
        <Dialog open={isInviteDialogOpen} onOpenChange={(open) => {
          setIsInviteDialogOpen(open)
          if (!open) resetInviteForm()
        }}>
          <DialogTrigger asChild>
            <Button>
              <UserPlus className="h-4 w-4 mr-2" />
              Convidar Usuário
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Convidar Usuário</DialogTitle>
              <DialogDescription>Envie um convite para um novo usuário</DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <div className="space-y-2">
                <Label>Email *</Label>
                <Input
                  type="email"
                  value={inviteFormData.email}
                  onChange={(e) => setInviteFormData({...inviteFormData, email: e.target.value})}
                  placeholder="email@exemplo.ao"
                />
              </div>
              <div className="space-y-2">
                <Label>Função</Label>
                <Select value={inviteFormData.role} onValueChange={(value: UserRole) => setInviteFormData({...inviteFormData, role: value})}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Administrador</SelectItem>
                    <SelectItem value="manager">Gerente</SelectItem>
                    <SelectItem value="accountant">Contabilista</SelectItem>
                    <SelectItem value="employee">Funcionário</SelectItem>
                    <SelectItem value="viewer">Visualizador</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex justify-end gap-3">
              <Button variant="outline" onClick={() => {
                setIsInviteDialogOpen(false)
                resetInviteForm()
              }}>
                Cancelar
              </Button>
              <Button onClick={handleInviteUser} disabled={inviteMutation.isPending}>
                {inviteMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Enviar Convite
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total de Usuários</CardTitle>
            <UsersIcon className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{users.length}</div>
            <p className="text-xs text-muted-foreground">
              {users.filter(u => u.is_active).length} ativos
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Administradores</CardTitle>
            <Shield className="h-4 w-4 text-destructive" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-destructive">
              {users.filter(u => u.role === 'admin').length}
            </div>
            <p className="text-xs text-muted-foreground">usuários</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Gerentes</CardTitle>
            <Shield className="h-4 w-4 text-primary" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-primary">
              {users.filter(u => u.role === 'manager').length}
            </div>
            <p className="text-xs text-muted-foreground">usuários</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Contabilistas</CardTitle>
            <UsersIcon className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {users.filter(u => u.role === 'accountant').length}
            </div>
            <p className="text-xs text-muted-foreground">usuários</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Filtros</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="relative">
              <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Buscar por nome ou email..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="pl-8"
              />
            </div>
            <Select value={filterRole} onValueChange={setFilterRole}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todas as Funções</SelectItem>
                <SelectItem value="admin">Administrador</SelectItem>
                <SelectItem value="manager">Gerente</SelectItem>
                <SelectItem value="accountant">Contabilista</SelectItem>
                <SelectItem value="employee">Funcionário</SelectItem>
                <SelectItem value="viewer">Visualizador</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Usuários ({filteredUsers.length})</CardTitle>
          <CardDescription>Lista de todos os usuários</CardDescription>
        </CardHeader>
        <CardContent>
          {filteredUsers.length === 0 ? (
            <div className="text-center py-12">
              <UsersIcon className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
              <p className="text-muted-foreground">Nenhum usuário encontrado</p>
            </div>
          ) : (
            <div className="space-y-4">
              {filteredUsers.map((user) => (
                <div key={user.id} className="flex items-center justify-between border-b pb-4 last:border-0">
                  <div className="flex-1">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                        <UsersIcon className="h-5 w-5 text-primary" />
                      </div>
                      <div>
                        <p className="font-medium">{user.name}</p>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                          <span>{user.email}</span>
                          <span>•</span>
                          <Badge variant={getRoleBadge(user.role).variant}>
                            {getRoleBadge(user.role).label}
                          </Badge>
                          <span>•</span>
                          <Badge variant={user.is_active ? 'default' : 'secondary'}>
                            {user.is_active ? 'Ativo' : 'Inativo'}
                          </Badge>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    <div className="text-right">
                      <p className="text-xs text-muted-foreground">
                        Criado: {new Date(user.created_at).toLocaleDateString('pt-AO')}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button 
                        variant="outline" 
                        size="sm" 
                        onClick={() => handleToggleStatus(user)}
                        disabled={toggleStatusMutation.isPending}
                      >
                        {user.is_active ? 'Desativar' : 'Ativar'}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => handleEditUser(user)}>
                        <Edit className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setDeleteUserId(user.id)}>
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Editar Função</DialogTitle>
            <DialogDescription>Altere a função do usuário {editingUser?.name}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="space-y-2">
              <Label>Função</Label>
              <Select value={editFormData} onValueChange={(value: UserRole) => setEditFormData(value)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">Administrador</SelectItem>
                  <SelectItem value="manager">Gerente</SelectItem>
                  <SelectItem value="accountant">Contabilista</SelectItem>
                  <SelectItem value="employee">Funcionário</SelectItem>
                  <SelectItem value="viewer">Visualizador</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={() => {
              setIsEditDialogOpen(false)
              setEditingUser(null)
            }}>
              Cancelar
            </Button>
            <Button onClick={handleUpdateRole} disabled={updateRoleMutation.isPending}>
              {updateRoleMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Atualizar
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={!!deleteUserId} onOpenChange={(open) => !open && setDeleteUserId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirmar Exclusão</AlertDialogTitle>
            <AlertDialogDescription>
              Tem certeza que deseja remover este usuário? Esta ação não pode ser desfeita.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteUser} disabled={deleteMutation.isPending}>
              {deleteMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Confirmar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}