// =====================================================
// KWANZACONTROL - Authentication Hook
// Gerencia autenticação com Supabase
// Data: 2026-04-04
// =====================================================

import React, { useState, useEffect, createContext, useContext } from 'react';
import type { ReactNode } from 'react';
import { User as SupabaseUser, Session } from '@supabase/supabase-js';
import { supabase } from '@/integrations/supabase/client';
import { Database } from '@/lib/supabase-types';

type UserProfile = Database['public']['Tables']['users']['Row'];
type Tenant = Database['public']['Tables']['tenants']['Row'];

interface AuthContextType {
  user: SupabaseUser | null;
  profile: UserProfile | null;
  tenant: Tenant | null;
  session: Session | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, fullName: string, tenantData: Partial<Tenant>) => Promise<void>;
  signOut: () => Promise<void>;
  updateProfile: (updates: Partial<UserProfile>) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SupabaseUser | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Get initial session
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setUser(session?.user ?? null);
      if (session?.user) {
        loadUserData(session.user.id);
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
        loadUserData(session.user.id);
      } else {
        setProfile(null);
        setTenant(null);
        setLoading(false);
      }
    });

    return () => subscription.unsubscribe();
  }, []);

  const loadUserData = async (userId: string) => {
    try {
      // Usar maybeSingle para evitar erro quando não há registo
      const { data: profileData, error: profileError } = await supabase
        .from('users')
        .select('*')
        .eq('id', userId)
        .maybeSingle();

      if (profileError) {
        console.warn('Error loading profile:', profileError);
        // Criar perfil mínimo a partir dos dados auth
        const { data: authData } = await supabase.auth.getUser();
        if (authData?.user) {
          // Tentar obter tenant_id de qualquer utilizador existente
          const { data: anyTenant } = await supabase
            .from('tenants')
            .select('id')
            .limit(1)
            .maybeSingle();

          const fallbackProfile = {
            id: authData.user.id,
            email: authData.user.email || '',
            full_name: authData.user.user_metadata?.full_name || authData.user.email?.split('@')[0] || 'Admin',
            role: 'ADMIN',
            is_active: true,
            tenant_id: anyTenant?.id || null,
          } as unknown as UserProfile;
          setProfile(fallbackProfile);

          if (anyTenant?.id) {
            const { data: tenantData } = await supabase
              .from('tenants')
              .select('*')
              .eq('id', anyTenant.id)
              .maybeSingle();
            setTenant(tenantData || null);
          }
        }
      } else if (profileData) {
        setProfile(profileData);

        if (profileData?.tenant_id) {
          const { data: tenantData, error: tenantError } = await supabase
            .from('tenants')
            .select('*')
            .eq('id', profileData.tenant_id)
            .maybeSingle();

          if (tenantError) {
            console.warn('Error loading tenant:', tenantError);
            setTenant(null);
          } else {
            setTenant(tenantData || null);
          }
        }
      } else {
        // Perfil não existe — criar automaticamente
        const { data: authData } = await supabase.auth.getUser();
        const { data: anyTenant } = await supabase
          .from('tenants')
          .select('id')
          .limit(1)
          .maybeSingle();

        if (authData?.user && anyTenant?.id) {
          const { data: newProfile } = await supabase
            .from('users')
            .upsert({
              id: userId,
              tenant_id: anyTenant.id,
              email: authData.user.email || '',
              full_name: authData.user.user_metadata?.full_name || authData.user.email?.split('@')[0] || 'Admin',
              role: 'ADMIN',
              is_active: true,
            })
            .select()
            .maybeSingle();

          setProfile(newProfile || null);
          if (anyTenant?.id) {
            const { data: tenantData } = await supabase
              .from('tenants')
              .select('*')
              .eq('id', anyTenant.id)
              .maybeSingle();
            setTenant(tenantData || null);
          }
        } else {
          setProfile(null);
          setTenant(null);
        }
      }
    } catch (error) {
      console.error('Error loading user data:', error);
      setProfile(null);
      setTenant(null);
    } finally {
      setLoading(false);
    }
  };

  const signIn = async (email: string, password: string) => {
    const { error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    if (error) throw error;
  };

  const signUp = async (
    email: string,
    password: string,
    fullName: string,
    tenantData: Partial<Tenant>
  ) => {
    // First, create tenant
    const { data: newTenant, error: tenantError } = await supabase
      .from('tenants')
      .insert({
        nif: tenantData.nif!,
        name: tenantData.name!,
        legal_name: tenantData.legal_name,
        address: tenantData.address,
        city: tenantData.city,
        phone: tenantData.phone,
        email: tenantData.email,
        industry: tenantData.industry,
        size: tenantData.size || 'SMALL',
        currency: 'AOA',
        timezone: 'Africa/Luanda',
        agt_certified: false,
        subscription_plan: 'BASIC',
        subscription_status: 'ACTIVE',
        settings: {},
      })
      .select()
      .single();

    if (tenantError) throw tenantError;

    // Then, sign up user
    const { error: signUpError } = await supabase.auth.signUp({
      email,
      password,
      options: {
        data: {
          full_name: fullName,
          tenant_id: newTenant.id,
          role: 'OWNER',
        },
      },
    });

    if (signUpError) throw signUpError;
  };

  const signOut = async () => {
    const { error } = await supabase.auth.signOut();
    if (error) throw error;
  };

  const updateProfile = async (updates: Partial<UserProfile>) => {
    if (!user) throw new Error('No user logged in');

    const { error } = await supabase
      .from('users')
      .update(updates)
      .eq('id', user.id);

    if (error) throw error;

    // Reload profile
    await loadUserData(user.id);
  };

  const value = {
    user,
    profile,
    tenant,
    session,
    loading,
    signIn,
    signUp,
    signOut,
    updateProfile,
  };

  return React.createElement(AuthContext.Provider, { value }, children);
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
