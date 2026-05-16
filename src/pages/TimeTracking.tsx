// TimeTracking, ExpenseTracking, BudgetManagement, ContractsManagement - Versões Rápidas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Clock, Play, Pause, Calendar } from 'lucide-react';

interface TimeEntry {
  id: string;
  user: string;
  project: string;
  task: string;
  hours: number;
  date: string;
  status: 'active' | 'paused' | 'completed';
}

export default function TimeTracking() {
  const [entries] = useState<TimeEntry[]>(
    Array.from({ length: 15 }, (_, i) => ({
      id: `time-${i + 1}`,
      user: ['João Silva', 'Maria Santos', 'Pedro Costa'][Math.floor(Math.random() * 3)],
      project: `Projeto ${String.fromCharCode(65 + (i % 3))}`,
      task: `Tarefa ${i + 1}`,
      hours: Math.floor(Math.random() * 8) + 1,
      date: new Date(Date.now() - Math.random() * 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
      status: ['active', 'paused', 'completed'][Math.floor(Math.random() * 3)] as TimeEntry['status']
    }))
  );

  const totalHours = entries.reduce((sum, e) => sum + e.hours, 0);
  const activeEntries = entries.filter(e => e.status === 'active');

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Controle de Tempo</h1>
          <p className="text-muted-foreground">Registro de horas trabalhadas</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Horas</CardTitle>
              <Clock className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{totalHours}h</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Registros Ativos</CardTitle>
              <Play className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{activeEntries.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Média Diária</CardTitle>
              <Calendar className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{Math.round(totalHours / 7)}h</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Registros</CardTitle>
              <Clock className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{entries.length}</div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Registros de Tempo</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {entries.map((entry) => (
                <div key={entry.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{entry.user}</p>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <span>{entry.project}</span>
                      <span>•</span>
                      <span>{entry.task}</span>
                      <span>•</span>
                      <span>{new Date(entry.date).toLocaleDateString('pt-AO')}</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-lg font-bold">{entry.hours}h</p>
                    <Badge variant={entry.status === 'active' ? 'default' : entry.status === 'paused' ? 'secondary' : 'outline'}>
                      {entry.status === 'active' ? 'Ativo' : entry.status === 'paused' ? 'Pausado' : 'Concluído'}
                    </Badge>
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
