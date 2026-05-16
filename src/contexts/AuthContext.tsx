// Authentication Context for KwanzaControl
import { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import { User, Session, AuthError } from '@supabase/supabase-js';
import { supabase } from '@/integrations/supabase/client';
import { toast } from 'sonner';

interface UserProfile {
  id: string;
  full_name: string;
  email: string;
  phone?: string;
  avatar_url?: string;
  role: string;
  is_active: boolean;
  tenant_id?: string;
  department?: string;
  position?: string;
}

interface AuthContextType {
  user: User | null;
  profile: UserProfile | null;
  session: Session | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<{ error: AuthError | null }>;
  signUp: (email: string, password: string, fullName: string) => Promise<{ error: AuthError | null }>;
  signOut: () => Promise<void>;
  resetPassword: (email: string) => Promise<{ error: AuthError | null }>;
  updateProfile: (updates: Partial<UserProfile>) => Promise<{ error: Error | null }>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Get initial session
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setUser(session?.user ?? null);
      if (session?.user) {
        loadUserProfile(session.user.id);
      } else {
        setLoading(false);
      }
    });

    // Listen for auth changes
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
      setUser(session?.user ?? null);
      if (session?.user) {
        loadUserProfile(session.user.id);
      } else {
        setProfile(null);
        setLoading(false);
      }
    });

    return () => subscription.unsubscribe();
  }, []);

  const loadUserProfile = async (userId: string) => {
    try {
      const { data, error } = await supabase
        .from('users')
        .select('id, full_name, email, phone, avatar_url, role, is_active, tenant_id, department, position')
        .eq('id', userId)
        .maybeSingle();

      if (error) {
        console.warn('Error loading profile:', error);
        setProfile(null);
      } else if (data) {
        setProfile(data as unknown as UserProfile);
      } else {
        // Perfil não existe ainda — criar a partir dos dados auth
        const { data: authUser } = await supabase.auth.getUser();
        if (authUser?.user) {
          const newProfile: UserProfile = {
            id: authUser.user.id,
            email: authUser.user.email || '',
            full_name: authUser.user.user_metadata?.full_name || authUser.user.email?.split('@')[0] || 'Utilizador',
            role: 'ADMIN',
            is_active: true,
          };
          setProfile(newProfile);
        } else {
          setProfile(null);
        }
      }
    } catch (error) {
      console.error('Error loading profile:', error);
      setProfile(null);
    } finally {
      setLoading(false);
    }
  };

  const signIn = async (email: string, password: string) => {
    try {
      const { error } = await supabase.auth.signInWithPassword({
        email,
        password,
      });
      
      if (error) {
        toast.error('Erro ao fazer login: ' + error.message);
        return { error };
      }
      
      toast.success('Login realizado com sucesso!');
      return { error: null };
    } catch (error) {
      const authError = error as AuthError;
      toast.error('Erro ao fazer login');
      return { error: authError };
    }
  };

  const signUp = async (email: string, password: string, fullName: string) => {
    try {
      const { error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          data: {
            full_name: fullName,
          },
        },
      });

      if (error) {
        toast.error('Erro ao criar conta: ' + error.message);
        return { error };
      }

      toast.success('Conta criada com sucesso! Verifique seu email.');
      return { error: null };
    } catch (error) {
      const authError = error as AuthError;
      toast.error('Erro ao criar conta');
      return { error: authError };
    }
  };

  const signOut = async () => {
    try {
      const { error } = await supabase.auth.signOut();
      if (error) throw error;
      toast.success('Logout realizado com sucesso!');
    } catch (error) {
      console.error('Error signing out:', error);
      toast.error('Erro ao fazer logout');
    }
  };

  const resetPassword = async (email: string) => {
    try {
      const { error } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/reset-password`,
      });

      if (error) {
        toast.error('Erro ao enviar email: ' + error.message);
        return { error };
      }

      toast.success('Email de recuperação enviado!');
      return { error: null };
    } catch (error) {
      const authError = error as AuthError;
      toast.error('Erro ao enviar email de recuperação');
      return { error: authError };
    }
  };

  const updateProfile = async (updates: Partial<UserProfile>) => {
    if (!user) return { error: new Error('Usuário não autenticado') };

    try {
      const { error } = await supabase
        .from('users')
        .update(updates)
        .eq('id', user.id);

      if (error) throw error;

      // Reload profile
      await loadUserProfile(user.id);
      toast.success('Perfil atualizado com sucesso!');
      return { error: null };
    } catch (error) {
      console.error('Error updating profile:', error);
      toast.error('Erro ao atualizar perfil');
      return { error: error as Error };
    }
  };

  const value = {
    user,
    profile,
    session,
    loading,
    signIn,
    signUp,
    signOut,
    resetPassword,
    updateProfile,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
