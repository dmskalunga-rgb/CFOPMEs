import { supabase } from '@/integrations/supabase/client'

export type UserRole = 'admin' | 'manager' | 'accountant' | 'employee' | 'viewer'

export interface User {
  id: string
  email: string
  name: string
  role: UserRole
  tenant_id: string
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface UserInvite {
  id: string
  email: string
  role: UserRole
  tenant_id: string
  invited_by: string
  status: 'pending' | 'accepted' | 'cancelled'
  expires_at: string
  created_at: string
  accepted_at?: string
}

export interface CreateUserData {
  email: string
  name: string
  role: UserRole
  tenantId: string
}

export interface UpdateUserData {
  name?: string
  role?: UserRole
  isActive?: boolean
}

export interface InviteUserData {
  email: string
  role: UserRole
  tenantId: string
  invitedBy: string
}

class UsersServiceReal {
  async getUsersByTenant(tenantId: string): Promise<User[]> {
    const { data, error } = await supabase
      .from('users')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false })

    if (error) {
      throw new Error(`Failed to fetch users: ${error.message}`)
    }

    return data || []
  }

  async getUserById(userId: string): Promise<User | null> {
    const { data, error } = await supabase
      .from('users')
      .select('*')
      .eq('id', userId)
      .single()

    if (error) {
      if (error.code === 'PGRST116') {
        return null
      }
      throw new Error(`Failed to fetch user: ${error.message}`)
    }

    return data
  }

  async getUserByEmail(email: string, tenantId: string): Promise<User | null> {
    const { data, error } = await supabase
      .from('users')
      .select('*')
      .eq('email', email)
      .eq('tenant_id', tenantId)
      .single()

    if (error) {
      if (error.code === 'PGRST116') {
        return null
      }
      throw new Error(`Failed to fetch user by email: ${error.message}`)
    }

    return data
  }

  async createUser(userData: CreateUserData): Promise<User> {
    const { data, error } = await supabase
      .from('users')
      .insert({
        email: userData.email,
        name: userData.name,
        role: userData.role,
        tenant_id: userData.tenantId,
        is_active: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString()
      })
      .select()
      .single()

    if (error) {
      throw new Error(`Failed to create user: ${error.message}`)
    }

    return data
  }

  async updateUser(userId: string, updates: UpdateUserData): Promise<User> {
    const updateData: Record<string, unknown> = {
      updated_at: new Date().toISOString()
    }

    if (updates.name !== undefined) {
      updateData.name = updates.name
    }
    if (updates.role !== undefined) {
      updateData.role = updates.role
    }
    if (updates.isActive !== undefined) {
      updateData.is_active = updates.isActive
    }

    const { data, error } = await supabase
      .from('users')
      .update(updateData)
      .eq('id', userId)
      .select()
      .single()

    if (error) {
      throw new Error(`Failed to update user: ${error.message}`)
    }

    return data
  }

  async updateUserRole(userId: string, role: UserRole): Promise<User> {
    return this.updateUser(userId, { role })
  }

  async activateUser(userId: string): Promise<User> {
    return this.updateUser(userId, { isActive: true })
  }

  async deactivateUser(userId: string): Promise<User> {
    return this.updateUser(userId, { isActive: false })
  }

  async deleteUser(userId: string): Promise<void> {
    const { error } = await supabase
      .from('users')
      .delete()
      .eq('id', userId)

    if (error) {
      throw new Error(`Failed to delete user: ${error.message}`)
    }
  }

  async inviteUser(inviteData: InviteUserData): Promise<UserInvite> {
    const existingUser = await this.getUserByEmail(inviteData.email, inviteData.tenantId)
    if (existingUser) {
      throw new Error('User with this email already exists in the tenant')
    }

    const { data: existingInvite } = await supabase
      .from('user_invites')
      .select('*')
      .eq('email', inviteData.email)
      .eq('tenant_id', inviteData.tenantId)
      .eq('status', 'pending')
      .single()

    if (existingInvite) {
      throw new Error('A pending invite already exists for this email')
    }

    const { data, error } = await supabase
      .from('user_invites')
      .insert({
        email: inviteData.email,
        role: inviteData.role,
        tenant_id: inviteData.tenantId,
        invited_by: inviteData.invitedBy,
        status: 'pending',
        expires_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
        created_at: new Date().toISOString()
      })
      .select()
      .single()

    if (error) {
      throw new Error(`Failed to create invite: ${error.message}`)
    }

    return data
  }

  async getPendingInvites(tenantId: string): Promise<UserInvite[]> {
    const { data, error } = await supabase
      .from('user_invites')
      .select('*')
      .eq('tenant_id', tenantId)
      .eq('status', 'pending')
      .gt('expires_at', new Date().toISOString())
      .order('created_at', { ascending: false })

    if (error) {
      throw new Error(`Failed to fetch invites: ${error.message}`)
    }

    return data || []
  }

  async acceptInvite(inviteId: string, _userId: string): Promise<User> {
    const { data: invite, error: inviteError } = await supabase
      .from('user_invites')
      .select('*')
      .eq('id', inviteId)
      .single()

    if (inviteError || !invite) {
      throw new Error('Invite not found')
    }

    if (invite.status !== 'pending') {
      throw new Error('Invite is no longer valid')
    }

    if (new Date(invite.expires_at) < new Date()) {
      throw new Error('Invite has expired')
    }

    const user = await this.createUser({
      email: invite.email,
      name: invite.email.split('@')[0],
      role: invite.role,
      tenantId: invite.tenant_id
    })

    await supabase
      .from('user_invites')
      .update({
        status: 'accepted',
        accepted_at: new Date().toISOString()
      })
      .eq('id', inviteId)

    return user
  }

  async cancelInvite(inviteId: string): Promise<void> {
    const { error } = await supabase
      .from('user_invites')
      .update({
        status: 'cancelled'
      })
      .eq('id', inviteId)

    if (error) {
      throw new Error(`Failed to cancel invite: ${error.message}`)
    }
  }

  async resendInvite(inviteId: string): Promise<UserInvite> {
    const { data, error } = await supabase
      .from('user_invites')
      .update({
        expires_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
        status: 'pending'
      })
      .eq('id', inviteId)
      .select()
      .single()

    if (error) {
      throw new Error(`Failed to resend invite: ${error.message}`)
    }

    return data
  }

  async getUsersByRole(tenantId: string, role: UserRole): Promise<User[]> {
    const { data, error } = await supabase
      .from('users')
      .select('*')
      .eq('tenant_id', tenantId)
      .eq('role', role)
      .order('created_at', { ascending: false })

    if (error) {
      throw new Error(`Failed to fetch users by role: ${error.message}`)
    }

    return data || []
  }

  async getActiveUsersCount(tenantId: string): Promise<number> {
    const { count, error } = await supabase
      .from('users')
      .select('*', { count: 'exact', head: true })
      .eq('tenant_id', tenantId)
      .eq('is_active', true)

    if (error) {
      throw new Error(`Failed to count active users: ${error.message}`)
    }

    return count || 0
  }

  async searchUsers(tenantId: string, query: string): Promise<User[]> {
    const { data, error } = await supabase
      .from('users')
      .select('*')
      .eq('tenant_id', tenantId)
      .or(`name.ilike.%${query}%,email.ilike.%${query}%`)
      .order('created_at', { ascending: false })

    if (error) {
      throw new Error(`Failed to search users: ${error.message}`)
    }

    return data || []
  }
}

export const usersService = new UsersServiceReal()
