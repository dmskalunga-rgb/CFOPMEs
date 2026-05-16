// KWANZACONTROL - Roadmap Interativo com Backend Real
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Progress } from '@/components/ui/progress';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useToast } from '@/hooks/use-toast';
import {
  CheckCircle2,
  Circle,
  Clock,
  TrendingUp,
  Target,
  Rocket,
  CreditCard,
  Bell,
  BarChart3,
  Smartphone,
  Building,
  Plug,
  Zap,
  Palette,
  Shield,
  Store,
  Map,
  Loader2,
  Calendar,
  Users,
  DollarSign,
  AlertCircle,
  ChevronRight,
} from 'lucide-react';
import { PageLoader } from '@/components/LoadingStates';
import { motion } from 'framer-motion';
import { roadmapService, Phase, Task, Milestone, RoadmapStats } from '@/services/roadmapService';

// Icon mapping
const iconMap: Record<string, any> = {
  CreditCard, Bell, BarChart3, Smartphone, Building, Plug, Zap, Palette, Shield, TrendingUp, Store, Map,
  CheckCircle2, Circle, Clock, Target, Rocket, Users, DollarSign, Calendar
};

const statusColors = {
  completed: 'bg-green-500',
  in_progress: 'bg-blue-500',
  planned: 'bg-gray-400',
  on_hold: 'bg-yellow-500',
  cancelled: 'bg-red-500',
};

const priorityColors = {
  critical: 'destructive',
  high: 'default',
  medium: 'secondary',
  low: 'outline',
};

export default function RoadmapPage() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [phases, setPhases] = useState<Phase[]>([]);
  const [stats, setStats] = useState<RoadmapStats | null>(null);
  const [selectedPhase, setSelectedPhase] = useState<Phase | null>(null);
  const [phaseTasks, setPhaseTasks] = useState<Task[]>([]);
  const [phaseMilestones, setPhaseMilestones] = useState<Milestone[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('timeline');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [phasesData, statsData] = await Promise.all([
        roadmapService.listPhases(),
        roadmapService.getStats(),
      ]);

      setPhases(phasesData);
      setStats(statsData);
    } catch (error: any) {
      toast({
        title: 'Erro ao carregar roadmap',
        description: error.message,
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  const handlePhaseClick = async (phase: Phase) => {
    try {
      setSelectedPhase(phase);
      setDialogOpen(true);
      
      const details = await roadmapService.getPhase(phase.id);
      setPhaseTasks(details.tasks);
      setPhaseMilestones(details.milestones);
    } catch (error: any) {
      toast({
        title: 'Erro ao carregar detalhes',
        description: error.message,
        variant: 'destructive',
      });
    }
  };

  const getPhaseIcon = (iconName?: string) => {
    if (!iconName) return Rocket;
    return iconMap[iconName] || Rocket;
  };

  const getStatusLabel = (status: string) => {
    const labels: Record<string, string> = {
      completed: 'Concluído',
      in_progress: 'Em Progresso',
      planned: 'Planejado',
      on_hold: 'Em Espera',
      cancelled: 'Cancelado',
    };
    return labels[status] || status;
  };

  const getTaskStatusLabel = (status: string) => {
    const labels: Record<string, string> = {
      completed: 'Concluído',
      in_progress: 'Em Progresso',
      todo: 'A Fazer',
      blocked: 'Bloqueado',
      cancelled: 'Cancelado',
    };
    return labels[status] || status;
  };

  if (loading) return <PageLoader />;

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Roadmap & Próximos Passos</h1>
          <p className="text-muted-foreground">Planejamento e evolução do KWANZACONTROL</p>
        </div>

        {/* Stats Cards */}
        {stats && (
          <div className="grid gap-4 md:grid-cols-4">
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total de Fases</CardTitle>
                  <Map className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.total_phases}</div>
                  <p className="text-xs text-muted-foreground">{stats.completed_phases} concluídas</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Progresso Geral</CardTitle>
                  <TrendingUp className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{Number(stats.overall_progress).toFixed(0)}%</div>
                  <Progress value={Number(stats.overall_progress)} className="mt-2" />
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Tarefas</CardTitle>
                  <CheckCircle2 className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.completed_tasks}/{stats.total_tasks}</div>
                  <p className="text-xs text-muted-foreground">Concluídas</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Marcos</CardTitle>
                  <Target className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.achieved_milestones}/{stats.total_milestones}</div>
                  <p className="text-xs text-muted-foreground">Alcançados</p>
                </CardContent>
              </Card>
            </motion.div>
          </div>
        )}

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
            <TabsTrigger value="status">Por Status</TabsTrigger>
          </TabsList>

          {/* TIMELINE TAB */}
          <TabsContent value="timeline" className="space-y-4">
            <div className="space-y-4">
              {phases.map((phase, index) => {
                const Icon = getPhaseIcon(phase.icon);
                
                return (
                  <motion.div
                    key={phase.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: index * 0.1 }}
                  >
                    <Card 
                      className="cursor-pointer hover:shadow-lg transition-shadow"
                      onClick={() => handlePhaseClick(phase)}
                    >
                      <CardHeader>
                        <div className="flex items-start justify-between">
                          <div className="flex items-center gap-3">
                            <div className={`h-10 w-10 rounded-lg flex items-center justify-center ${statusColors[phase.status]}`}>
                              <Icon className="h-5 w-5 text-white" />
                            </div>
                            <div>
                              <CardTitle className="text-lg flex items-center gap-2">
                                Fase {phase.phase_number}: {phase.name}
                                <ChevronRight className="h-4 w-4 text-muted-foreground" />
                              </CardTitle>
                              <CardDescription>{phase.description}</CardDescription>
                            </div>
                          </div>
                          <div className="flex flex-col items-end gap-2">
                            <Badge variant={priorityColors[phase.priority] as any}>
                              {phase.priority.toUpperCase()}
                            </Badge>
                            <Badge variant="outline">
                              {getStatusLabel(phase.status)}
                            </Badge>
                          </div>
                        </div>
                      </CardHeader>
                      <CardContent>
                        <div className="space-y-3">
                          <div className="flex items-center justify-between text-sm">
                            <span className="text-muted-foreground">Progresso</span>
                            <span className="font-medium">{phase.progress_percentage}%</span>
                          </div>
                          <Progress value={phase.progress_percentage} />
                          
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-2">
                            {phase.start_date && (
                              <div className="flex items-center gap-2 text-sm">
                                <Calendar className="h-4 w-4 text-muted-foreground" />
                                <span>{new Date(phase.start_date).toLocaleDateString('pt-AO')}</span>
                              </div>
                            )}
                            {phase.team_size && (
                              <div className="flex items-center gap-2 text-sm">
                                <Users className="h-4 w-4 text-muted-foreground" />
                                <span>{phase.team_size} pessoas</span>
                              </div>
                            )}
                            {phase.budget && (
                              <div className="flex items-center gap-2 text-sm">
                                <DollarSign className="h-4 w-4 text-muted-foreground" />
                                <span>{new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA' }).format(phase.budget)}</span>
                              </div>
                            )}
                            {phase.estimated_duration_days && (
                              <div className="flex items-center gap-2 text-sm">
                                <Clock className="h-4 w-4 text-muted-foreground" />
                                <span>{phase.estimated_duration_days} dias</span>
                              </div>
                            )}
                          </div>

                          {phase.tags && phase.tags.length > 0 && (
                            <div className="flex flex-wrap gap-2 pt-2">
                              {phase.tags.map((tag, i) => (
                                <Badge key={i} variant="secondary" className="text-xs">
                                  {tag}
                                </Badge>
                              ))}
                            </div>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  </motion.div>
                );
              })}
            </div>
          </TabsContent>

          {/* STATUS TAB */}
          <TabsContent value="status" className="space-y-6">
            {(['completed', 'in_progress', 'planned'] as const).map((status) => {
              const filteredPhases = phases.filter(p => p.status === status);
              if (filteredPhases.length === 0) return null;

              return (
                <div key={status}>
                  <h3 className="text-lg font-semibold mb-3 flex items-center gap-2">
                    <div className={`h-3 w-3 rounded-full ${statusColors[status as keyof typeof statusColors]}`} />
                    {getStatusLabel(status)} ({filteredPhases.length})
                  </h3>
                  <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {filteredPhases.map((phase) => {
                      const Icon = getPhaseIcon(phase.icon);
                      
                      return (
                        <Card 
                          key={phase.id}
                          className="cursor-pointer hover:shadow-lg transition-shadow"
                          onClick={() => handlePhaseClick(phase)}
                        >
                          <CardHeader>
                            <div className="flex items-center gap-3">
                              <div className={`h-8 w-8 rounded-lg flex items-center justify-center ${statusColors[phase.status]}`}>
                                <Icon className="h-4 w-4 text-white" />
                              </div>
                              <div className="flex-1">
                                <CardTitle className="text-base">Fase {phase.phase_number}</CardTitle>
                                <CardDescription className="text-xs">{phase.name}</CardDescription>
                              </div>
                            </div>
                          </CardHeader>
                          <CardContent>
                            <Progress value={phase.progress_percentage} className="mb-2" />
                            <p className="text-xs text-muted-foreground">{phase.progress_percentage}% concluído</p>
                          </CardContent>
                        </Card>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </TabsContent>
        </Tabs>

        {/* Phase Details Dialog */}
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
            {selectedPhase && (
              <>
                <DialogHeader>
                  <DialogTitle className="flex items-center gap-3">
                    {(() => {
                      const Icon = getPhaseIcon(selectedPhase.icon);
                      return <Icon className="h-6 w-6" />;
                    })()}
                    Fase {selectedPhase.phase_number}: {selectedPhase.name}
                  </DialogTitle>
                  <DialogDescription>{selectedPhase.description}</DialogDescription>
                </DialogHeader>

                <div className="space-y-6">
                  {/* Phase Info */}
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <p className="text-sm text-muted-foreground">Status</p>
                      <Badge variant="outline">{getStatusLabel(selectedPhase.status)}</Badge>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Prioridade</p>
                      <Badge variant={priorityColors[selectedPhase.priority] as any}>
                        {selectedPhase.priority.toUpperCase()}
                      </Badge>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Progresso</p>
                      <p className="font-medium">{selectedPhase.progress_percentage}%</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Duração</p>
                      <p className="font-medium">{selectedPhase.estimated_duration_days} dias</p>
                    </div>
                  </div>

                  {/* Tasks */}
                  {phaseTasks.length > 0 && (
                    <div>
                      <h4 className="font-semibold mb-3">Tarefas ({phaseTasks.length})</h4>
                      <div className="space-y-2">
                        {phaseTasks.map((task) => (
                          <div key={task.id} className="flex items-center justify-between p-3 border rounded-lg">
                            <div className="flex items-center gap-3">
                              {task.status === 'completed' ? (
                                <CheckCircle2 className="h-5 w-5 text-green-500" />
                              ) : task.status === 'in_progress' ? (
                                <Loader2 className="h-5 w-5 text-blue-500 animate-spin" />
                              ) : (
                                <Circle className="h-5 w-5 text-gray-400" />
                              )}
                              <div>
                                <p className="font-medium">{task.name}</p>
                                {task.description && (
                                  <p className="text-sm text-muted-foreground">{task.description}</p>
                                )}
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              <Badge variant="outline" className="text-xs">
                                {getTaskStatusLabel(task.status)}
                              </Badge>
                              {task.progress_percentage > 0 && (
                                <span className="text-sm text-muted-foreground">
                                  {task.progress_percentage}%
                                </span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Milestones */}
                  {phaseMilestones.length > 0 && (
                    <div>
                      <h4 className="font-semibold mb-3">Marcos ({phaseMilestones.length})</h4>
                      <div className="space-y-2">
                        {phaseMilestones.map((milestone) => (
                          <div key={milestone.id} className="p-3 border rounded-lg">
                            <div className="flex items-center justify-between mb-2">
                              <div className="flex items-center gap-2">
                                <Target className="h-5 w-5 text-primary" />
                                <p className="font-medium">{milestone.name}</p>
                              </div>
                              <Badge 
                                variant={milestone.status === 'achieved' ? 'default' : 'outline'}
                              >
                                {milestone.status === 'achieved' ? 'Alcançado' : 'Pendente'}
                              </Badge>
                            </div>
                            {milestone.description && (
                              <p className="text-sm text-muted-foreground mb-2">{milestone.description}</p>
                            )}
                            <div className="flex items-center gap-4 text-sm text-muted-foreground">
                              <span>Meta: {new Date(milestone.target_date).toLocaleDateString('pt-AO')}</span>
                              {milestone.achieved_date && (
                                <span>Alcançado: {new Date(milestone.achieved_date).toLocaleDateString('pt-AO')}</span>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
}
