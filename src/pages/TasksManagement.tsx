// TasksManagement - Gestão de Tarefas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CheckCircle, Clock, AlertCircle, ListTodo, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

interface Task {
  id: string;
  title: string;
  description: string;
  assignee: string;
  priority: 'high' | 'medium' | 'low';
  status: 'todo' | 'in_progress' | 'completed' | 'blocked';
  due_date: string;
  project: string;
}

const generateTasks = (): Task[] => {
  const priorities: Task['priority'][] = ['high', 'medium', 'low'];
  const statuses: Task['status'][] = ['todo', 'in_progress', 'completed', 'blocked'];
  const assignees = ['João Silva', 'Maria Santos', 'Pedro Costa', 'Ana Oliveira'];
  const projects = ['Projeto A', 'Projeto B', 'Projeto C'];
  
  return Array.from({ length: 20 }, (_, i) => ({
    id: `task-${i + 1}`,
    title: `Tarefa ${i + 1}`,
    description: `Descrição da tarefa ${i + 1}`,
    assignee: assignees[Math.floor(Math.random() * assignees.length)],
    priority: priorities[Math.floor(Math.random() * priorities.length)],
    status: statuses[Math.floor(Math.random() * statuses.length)],
    due_date: new Date(Date.now() + (Math.random() * 30 - 15) * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
    project: projects[Math.floor(Math.random() * projects.length)]
  }));
};

export default function TasksManagement() {
  const [tasks, setTasks] = useState<Task[]>(generateTasks());
  const [activeTab, setActiveTab] = useState('all');

  const todoTasks = tasks.filter(t => t.status === 'todo');
  const inProgressTasks = tasks.filter(t => t.status === 'in_progress');
  const completedTasks = tasks.filter(t => t.status === 'completed');
  const blockedTasks = tasks.filter(t => t.status === 'blocked');

  const handleStatusChange = (taskId: string, newStatus: Task['status']) => {
    setTasks(tasks.map(t => t.id === taskId ? { ...t, status: newStatus } : t));
    toast.success('Status atualizado!');
  };

  const getPriorityBadge = (priority: Task['priority']) => {
    const variants = {
      high: { label: 'Alta', variant: 'destructive' as const },
      medium: { label: 'Média', variant: 'default' as const },
      low: { label: 'Baixa', variant: 'secondary' as const }
    };
    return variants[priority];
  };

  const getStatusBadge = (status: Task['status']) => {
    const variants = {
      todo: { label: 'A Fazer', variant: 'secondary' as const, icon: ListTodo },
      in_progress: { label: 'Em Progresso', variant: 'default' as const, icon: Clock },
      completed: { label: 'Concluída', variant: 'default' as const, icon: CheckCircle },
      blocked: { label: 'Bloqueada', variant: 'destructive' as const, icon: AlertCircle }
    };
    return variants[status];
  };

  const renderTaskList = (taskList: Task[]) => (
    <div className="space-y-3">
      {taskList.map((task) => {
        const status = getStatusBadge(task.status);
        const StatusIcon = status.icon;
        return (
          <div key={task.id} className="border rounded-lg p-4">
            <div className="flex items-start justify-between mb-2">
              <div className="flex-1">
                <h3 className="font-semibold">{task.title}</h3>
                <p className="text-sm text-muted-foreground">{task.description}</p>
              </div>
              <Badge variant={getPriorityBadge(task.priority).variant}>
                {getPriorityBadge(task.priority).label}
              </Badge>
            </div>
            <div className="flex items-center justify-between mt-3 pt-3 border-t">
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <span>{task.assignee}</span>
                <span>•</span>
                <span>{task.project}</span>
                <span>•</span>
                <span>{new Date(task.due_date).toLocaleDateString('pt-AO')}</span>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant={status.variant}>
                  <StatusIcon className="h-3 w-3 mr-1" />
                  {status.label}
                </Badge>
                {task.status !== 'completed' && (
                  <Button size="sm" variant="outline" onClick={() => handleStatusChange(task.id, 'completed')}>
                    Concluir
                  </Button>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Gestão de Tarefas</h1>
            <p className="text-muted-foreground">Controle de tarefas e atividades</p>
          </div>
          <Button variant="outline" onClick={() => setTasks(generateTasks())}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Atualizar
          </Button>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total</CardTitle>
              <ListTodo className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{tasks.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">A Fazer</CardTitle>
              <ListTodo className="h-4 w-4 text-blue-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-600">{todoTasks.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Em Progresso</CardTitle>
              <Clock className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-yellow-600">{inProgressTasks.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Concluídas</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{completedTasks.length}</div>
            </CardContent>
          </Card>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-5">
            <TabsTrigger value="all">Todas ({tasks.length})</TabsTrigger>
            <TabsTrigger value="todo">A Fazer ({todoTasks.length})</TabsTrigger>
            <TabsTrigger value="in_progress">Em Progresso ({inProgressTasks.length})</TabsTrigger>
            <TabsTrigger value="completed">Concluídas ({completedTasks.length})</TabsTrigger>
            <TabsTrigger value="blocked">Bloqueadas ({blockedTasks.length})</TabsTrigger>
          </TabsList>

          <TabsContent value="all">
            <Card>
              <CardHeader>
                <CardTitle>Todas as Tarefas</CardTitle>
              </CardHeader>
              <CardContent>{renderTaskList(tasks)}</CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="todo">
            <Card>
              <CardHeader>
                <CardTitle>Tarefas a Fazer</CardTitle>
              </CardHeader>
              <CardContent>{renderTaskList(todoTasks)}</CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="in_progress">
            <Card>
              <CardHeader>
                <CardTitle>Tarefas em Progresso</CardTitle>
              </CardHeader>
              <CardContent>{renderTaskList(inProgressTasks)}</CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="completed">
            <Card>
              <CardHeader>
                <CardTitle>Tarefas Concluídas</CardTitle>
              </CardHeader>
              <CardContent>{renderTaskList(completedTasks)}</CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="blocked">
            <Card>
              <CardHeader>
                <CardTitle>Tarefas Bloqueadas</CardTitle>
              </CardHeader>
              <CardContent>{renderTaskList(blockedTasks)}</CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
