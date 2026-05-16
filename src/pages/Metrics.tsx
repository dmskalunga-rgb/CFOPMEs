// =====================================================
// KWANZACONTROL - IAM/PAM Metrics Page
// Página de métricas e analytics IAM/PAM
// Data: 2026-04-04
// =====================================================

import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { BarChart3, TrendingUp } from 'lucide-react';
import { motion } from 'framer-motion';

export default function MetricsPage() {
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
                <BarChart3 className="h-8 w-8 text-primary" />
                Métricas IAM & PAM
              </h1>
              <p className="text-muted-foreground mt-2">
                Analytics e métricas de segurança, utilizadores e aprovações
              </p>
            </div>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
        >
          <Card>
            <CardHeader>
              <CardTitle>Métricas do Sistema</CardTitle>
              <CardDescription>Visualização de métricas e analytics</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-center text-muted-foreground py-8">
                Dashboard de métricas em desenvolvimento
              </p>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </Layout>
  );
}
