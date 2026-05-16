import { Layout } from '@/components/Layout';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { FinancialReports } from '@/components/FinancialReports';
import { CRMManagement } from '@/components/CRMManagement';
import { FileText, Users, Target } from 'lucide-react';

export default function BusinessManagement() {
  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Gestão Empresarial</h1>
          <p className="text-muted-foreground mt-1">
            Relatórios, CRM e Planejamento Financeiro
          </p>
        </div>

        <Tabs defaultValue="reports" className="w-full">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="reports" className="gap-2">
              <FileText className="h-4 w-4" />
              Relatórios
            </TabsTrigger>
            <TabsTrigger value="crm" className="gap-2">
              <Users className="h-4 w-4" />
              CRM / SRM
            </TabsTrigger>
            <TabsTrigger value="planning" className="gap-2">
              <Target className="h-4 w-4" />
              Planejamento
            </TabsTrigger>
          </TabsList>

          <TabsContent value="reports">
            <FinancialReports />
          </TabsContent>

          <TabsContent value="crm">
            <CRMManagement />
          </TabsContent>

          <TabsContent value="planning">
            <div className="text-center py-12">
              <Target className="h-16 w-16 text-muted-foreground mx-auto mb-4" />
              <h3 className="text-xl font-semibold mb-2">Planejamento Financeiro</h3>
              <p className="text-muted-foreground">
                Metas e projeções em desenvolvimento
              </p>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
