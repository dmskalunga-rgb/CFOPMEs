// =====================================================
// KWANZACONTROL - Advanced Security Page
// Página de segurança avançada (AD, SSO, WebAuthn, Compliance)
// Data: 2026-04-04
// =====================================================

import { Layout } from '@/components/Layout';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ActiveDirectoryConfig } from '@/components/ActiveDirectoryConfig';
import { SSOConfiguration } from '@/components/SSOConfiguration';
import { WebAuthnManagement } from '@/components/WebAuthnManagement';
import { ComplianceDashboard } from '@/components/ComplianceDashboard';
import { Shield, Server, KeyRound, Fingerprint, FileCheck } from 'lucide-react';
import { motion } from 'framer-motion';

export default function AdvancedSecurity() {
  return (
    <Layout>
      <div className="space-y-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
                <Shield className="h-8 w-8 text-primary" />
                Segurança Avançada
              </h1>
              <p className="text-muted-foreground mt-2">
                Active Directory, SSO, Biometria e Compliance
              </p>
            </div>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
        >
          <Tabs defaultValue="ad" className="space-y-6">
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="ad" className="flex items-center gap-2">
                <Server className="w-4 h-4" />
                Active Directory
              </TabsTrigger>
              <TabsTrigger value="sso" className="flex items-center gap-2">
                <KeyRound className="w-4 h-4" />
                SSO
              </TabsTrigger>
              <TabsTrigger value="webauthn" className="flex items-center gap-2">
                <Fingerprint className="w-4 h-4" />
                Biometria
              </TabsTrigger>
              <TabsTrigger value="compliance" className="flex items-center gap-2">
                <FileCheck className="w-4 h-4" />
                Compliance
              </TabsTrigger>
            </TabsList>

            <TabsContent value="ad">
              <ActiveDirectoryConfig />
            </TabsContent>

            <TabsContent value="sso">
              <SSOConfiguration />
            </TabsContent>

            <TabsContent value="webauthn">
              <WebAuthnManagement />
            </TabsContent>

            <TabsContent value="compliance">
              <ComplianceDashboard />
            </TabsContent>
          </Tabs>
        </motion.div>
      </div>
    </Layout>
  );
}
