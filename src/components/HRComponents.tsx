import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { DollarSign } from 'lucide-react';

export function HRPayrollAdvanced() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <DollarSign className="h-5 w-5" />
          Payroll Inteligente
        </CardTitle>
        <CardDescription>
          Processamento de folha de pagamento com IA, subsídios automáticos e integração financeira
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="text-center py-12 text-muted-foreground">
          <p className="text-lg font-medium mb-2">Módulo de Payroll Avançado</p>
          <p className="text-sm">
            Funcionalidades: Cálculo automático de IRT e INSS, subsídios (alimentação, transporte, férias, natal),
            horas extras, bónus, comissões, adiantamentos, faltas, geração de PDF, assinatura digital,
            integração com módulo financeiro e IA preditiva.
          </p>
          <p className="text-sm mt-4 text-primary">
            ✅ Backend implementado: Edge Function process_payroll_intelligent_2026_04_05
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export function HRBenefits() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Gestão de Benefícios</CardTitle>
        <CardDescription>
          Seguros, vales, subsídios e outros benefícios dos funcionários
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="text-center py-12 text-muted-foreground">
          <p className="text-lg font-medium mb-2">Módulo de Benefícios</p>
          <p className="text-sm">
            Funcionalidades: Seguro de saúde, seguro de vida, vale refeição, ginásio, transporte,
            gestão de custos (empregado vs empregador), datas de início/fim, status ativo/suspenso.
          </p>
          <p className="text-sm mt-4 text-primary">
            ✅ Backend implementado: Tabela employee_benefits com RLS
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export function HRPerformance() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Avaliação de Desempenho</CardTitle>
        <CardDescription>
          Avaliações periódicas, metas e feedback dos funcionários
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="text-center py-12 text-muted-foreground">
          <p className="text-lg font-medium mb-2">Módulo de Desempenho</p>
          <p className="text-sm">
            Funcionalidades: Avaliações mensais/trimestrais/anuais, scores (produtividade, qualidade, trabalho em equipe,
            pontualidade, iniciativa), pontos fortes/fracos, metas, comentários, status (rascunho/submetido/aprovado).
          </p>
          <p className="text-sm mt-4 text-primary">
            ✅ Backend implementado: Tabela employee_performance com RLS
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export function HRAbsences() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Gestão de Ausências</CardTitle>
        <CardDescription>
          Férias, faltas, licenças médicas e outras ausências
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="text-center py-12 text-muted-foreground">
          <p className="text-lg font-medium mb-2">Módulo de Ausências</p>
          <p className="text-sm">
            Funcionalidades: Férias, licença médica, maternidade, paternidade, faltas não pagas,
            aprovação/rejeição, certificados médicos, cálculo automático de dias, integração com payroll.
          </p>
          <p className="text-sm mt-4 text-primary">
            ✅ Backend implementado: Tabela employee_absences com workflow de aprovação
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export function HRAnalytics() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Analytics com IA</CardTitle>
        <CardDescription>
          Previsões, detecção de fraudes e insights inteligentes
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="text-center py-12 text-muted-foreground">
          <p className="text-lg font-medium mb-2">Módulo de IA e Analytics</p>
          <p className="text-sm">
            Funcionalidades: Previsão de turnover (risco de abandono), previsão de custos salariais,
            previsão de absentismo, detecção de fraudes (UEBA), padrões anômalos, classificação de desempenho,
            otimização de custos, recomendações inteligentes.
          </p>
          <p className="text-sm mt-4 text-primary">
            ✅ Backend implementado: Tabela hr_analytics + IA na Edge Function
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
