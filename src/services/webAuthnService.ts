// =====================================================
// KWANZACONTROL - WebAuthn Service
// Serviço de autenticação biométrica (WebAuthn/FIDO2)
// Data: 2026-04-04
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export interface WebAuthnCredential {
  id: string;
  user_id: string;
  credential_id: string;
  public_key: string;
  counter: number;
  device_type: 'platform' | 'cross-platform';
  device_name: string;
  created_at: string;
  last_used: string | null;
}

export interface RegistrationOptions {
  challenge: string;
  rp: {
    name: string;
    id: string;
  };
  user: {
    id: string;
    name: string;
    displayName: string;
  };
  pubKeyCredParams: Array<{
    type: 'public-key';
    alg: number;
  }>;
  timeout: number;
  attestation: 'none' | 'indirect' | 'direct';
  authenticatorSelection: {
    authenticatorAttachment?: 'platform' | 'cross-platform';
    requireResidentKey: boolean;
    userVerification: 'required' | 'preferred' | 'discouraged';
  };
}

export interface AuthenticationOptions {
  challenge: string;
  timeout: number;
  rpId: string;
  allowCredentials: Array<{
    type: 'public-key';
    id: string;
  }>;
  userVerification: 'required' | 'preferred' | 'discouraged';
}

export const webAuthnService = {
  /**
   * Verificar suporte do navegador
   */
  isSupported(): boolean {
    return (
      window.PublicKeyCredential !== undefined &&
      navigator.credentials !== undefined
    );
  },

  /**
   * Verificar disponibilidade de autenticador de plataforma
   */
  async isPlatformAuthenticatorAvailable(): Promise<boolean> {
    if (!this.isSupported()) return false;
    
    try {
      return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
    } catch {
      return false;
    }
  },

  /**
   * Iniciar registro de credencial
   */
  async startRegistration(userId: string, deviceName: string): Promise<RegistrationOptions> {
    const { data, error } = await supabase.functions.invoke('webauthn-registration-start', {
      body: { user_id: userId, device_name: deviceName },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Completar registro de credencial
   */
  async completeRegistration(
    userId: string,
    credential: PublicKeyCredential,
    deviceName: string
  ): Promise<WebAuthnCredential> {
    const { data, error } = await supabase.functions.invoke('webauthn-registration-complete', {
      body: {
        user_id: userId,
        credential: {
          id: credential.id,
          rawId: Array.from(new Uint8Array(credential.rawId)),
          response: {
            clientDataJSON: Array.from(
              new Uint8Array((credential.response as AuthenticatorAttestationResponse).clientDataJSON)
            ),
            attestationObject: Array.from(
              new Uint8Array((credential.response as AuthenticatorAttestationResponse).attestationObject)
            ),
          },
          type: credential.type,
        },
        device_name: deviceName,
      },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Registrar credencial (fluxo completo)
   */
  async registerCredential(userId: string, deviceName: string): Promise<WebAuthnCredential> {
    // 1. Obter opções de registro
    const options = await this.startRegistration(userId, deviceName);

    // 2. Converter challenge de base64 para ArrayBuffer
    const challenge = Uint8Array.from(atob(options.challenge), c => c.charCodeAt(0));
    const userId_buffer = Uint8Array.from(atob(options.user.id), c => c.charCodeAt(0));

    // 3. Criar credencial
    const credential = await navigator.credentials.create({
      publicKey: {
        ...options,
        challenge,
        user: {
          ...options.user,
          id: userId_buffer,
        },
      },
    }) as PublicKeyCredential;

    if (!credential) {
      throw new Error('Falha ao criar credencial');
    }

    // 4. Completar registro
    return await this.completeRegistration(userId, credential, deviceName);
  },

  /**
   * Iniciar autenticação
   */
  async startAuthentication(userId: string): Promise<AuthenticationOptions> {
    const { data, error } = await supabase.functions.invoke('webauthn-authentication-start', {
      body: { user_id: userId },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Completar autenticação
   */
  async completeAuthentication(
    userId: string,
    credential: PublicKeyCredential
  ): Promise<{ success: boolean; session?: any }> {
    const { data, error } = await supabase.functions.invoke('webauthn-authentication-complete', {
      body: {
        user_id: userId,
        credential: {
          id: credential.id,
          rawId: Array.from(new Uint8Array(credential.rawId)),
          response: {
            clientDataJSON: Array.from(
              new Uint8Array((credential.response as AuthenticatorAssertionResponse).clientDataJSON)
            ),
            authenticatorData: Array.from(
              new Uint8Array((credential.response as AuthenticatorAssertionResponse).authenticatorData)
            ),
            signature: Array.from(
              new Uint8Array((credential.response as AuthenticatorAssertionResponse).signature)
            ),
            userHandle: (credential.response as AuthenticatorAssertionResponse).userHandle
              ? Array.from(new Uint8Array((credential.response as AuthenticatorAssertionResponse).userHandle!))
              : null,
          },
          type: credential.type,
        },
      },
    });

    if (error) throw error;
    return data;
  },

  /**
   * Autenticar (fluxo completo)
   */
  async authenticate(userId: string): Promise<{ success: boolean; session?: any }> {
    // 1. Obter opções de autenticação
    const options = await this.startAuthentication(userId);

    // 2. Converter challenge de base64 para ArrayBuffer
    const challenge = Uint8Array.from(atob(options.challenge), c => c.charCodeAt(0));
    const allowCredentials = options.allowCredentials.map((cred: { type: 'public-key'; id: string }) => ({
      ...cred,
      id: Uint8Array.from(atob(cred.id), c => c.charCodeAt(0)),
    }));

    // 3. Obter credencial
    const credential = await navigator.credentials.get({
      publicKey: {
        ...options,
        challenge,
        allowCredentials,
      },
    }) as PublicKeyCredential;

    if (!credential) {
      throw new Error('Falha ao obter credencial');
    }

    // 4. Completar autenticação
    return await this.completeAuthentication(userId, credential);
  },

  /**
   * Listar credenciais do utilizador
   */
  async getUserCredentials(userId: string): Promise<WebAuthnCredential[]> {
    const { data, error } = await supabase
      .from('webauthn_credentials')
      .select('*')
      .eq('user_id', userId)
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data || [];
  },

  /**
   * Remover credencial
   */
  async removeCredential(credentialId: string): Promise<void> {
    const { error } = await supabase
      .from('webauthn_credentials')
      .delete()
      .eq('id', credentialId);

    if (error) throw error;
  },

  /**
   * Renomear credencial
   */
  async renameCredential(credentialId: string, newName: string): Promise<void> {
    const { error } = await supabase
      .from('webauthn_credentials')
      .update({ device_name: newName })
      .eq('id', credentialId);

    if (error) throw error;
  },
};
