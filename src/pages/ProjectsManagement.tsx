// ProjectsManagement, TimeTracking, ExpenseTracking - Versões Concisas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { FolderKanban, Users, Calendar, TrendingUp } from 'lucide-react';

interface Project {
  id: string;
  name: string;
  client: string;
  status: 'planning' | 'active' | 'on_hold' | 'completed';
  progress: number;
  budget: number;
  spent: number;
  team_size: number;
  deadline: string;
}

export default function ProjectsManagement() {
  const [projects] = useState<Project[]>(
    Array.from({ length: 8 }, (_, i) => {
      const budget = Math.floor(Math.random() * 5000000) + 1000000;
      const spent = Math.floor(budget * (Math.random() * 0.8));
      return {
        id: `proj-${i + 1}`,
        name: `Projeto ${String.fromCharCode(65 + i)}`,
        client: `Cliente ${i + 1}`,
        status: ['planning', 'active', 'on_hold', 'completed'][Math.floor(Math.random() * 4)] as Project['status'],
        progress: Math.floor(Math.random() * 100),
        budget,
        spent,
        team_size: Math.floor(Math.random() * 10) + 3,
        deadline: new Date(Date.now() + Math.random() * 90 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
      };
    })
  );

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
  const totalBudget = projects.reduce((sum, p) => sum + p.budget, 0);
  const totalSpent = projects.reduce((sum, p) => sum + p.spent, 0);

  const getStatusBadge = (status: Project['status']) => {
    const variants = {
      planning: { label: 'Planejamento', variant: 'secondary' as const },
      active: { label: 'Ativo', variant: 'default' as const },
      on_hold: { label: 'Pausado', variant: 'secondary' as const },
      completed: { label: 'Concluído', variant: 'default' as const }
    };
    return variants[status];
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Gestão de Projetos</h1>
          <p className="text-muted-foreground">Controle de projetos e equipes</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Projetos</CardTitle>
              <FolderKanban className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{projects.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Orçamento Total</CardTitle>
              <TrendingUp className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{formatCurrency(totalBudget)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Gasto Total</CardTitle>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(totalSpent)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Projetos Ativos</CardTitle>
              <FolderKanban className="h-4 w-4 text-blue-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-600">
                {projects.filter(p => p.status === 'active').length}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Projetos</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {projects.map((project) => (
                <div key={project.id} className="border rounded-lg p-4">
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex-1">
                      <h3 className="font-semibold text-lg">{project.name}</h3>
                      <div className="flex items-center gap-2 text-sm text-muted-foreground mt-1">
                        <span>{project.client}</span>
                        <span>•</span>
                        <Badge variant={getStatusBadge(project.status).variant}>
                          {getStatusBadge(project.status).label}
                        </Badge>
                        <span>•</span>
                        <span className="flex items-center gap-1">
                          <Users className="h-3 w-3" />
                          {project.team_size}
                        </span>
                        <span>•</span>
                        <span className="flex items-center gap-1">
                          <Calendar className="h-3 w-3" />
                          {new Date(project.deadline).toLocaleDateString('pt-AO')}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span>Progresso</span>
                      <span className="font-medium">{project.progress}%</span>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <div className="h-full bg-primary" style={{ width: `${project.progress}%` }} />
                    </div>
                    <div className="flex justify-between text-sm pt-2">
                      <span className="text-muted-foreground">Orçamento: {formatCurrency(project.budget)}</span>
                      <span className="font-medium">Gasto: {formatCurrency(project.spent)} ({Math.round(project.spent / project.budget * 100)}%)</span>
                    </div>
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
