import { supabase } from '@/integrations/supabase/client';

export interface Phase {
  id: string;
  name: string;
  description?: string;
  phase_number: number;
  status: 'planned' | 'in_progress' | 'completed' | 'on_hold' | 'cancelled';
  priority: 'low' | 'medium' | 'high' | 'critical';
  start_date?: string;
  end_date?: string;
  estimated_duration_days?: number;
  actual_duration_days?: number;
  progress_percentage: number;
  budget?: number;
  actual_cost?: number;
  team_size?: number;
  dependencies?: string[];
  tags?: string[];
  color?: string;
  icon?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  phase_id: string;
  name: string;
  description?: string;
  status: 'todo' | 'in_progress' | 'completed' | 'blocked' | 'cancelled';
  priority: 'low' | 'medium' | 'high' | 'critical';
  assigned_to?: string;
  start_date?: string;
  due_date?: string;
  completed_date?: string;
  estimated_hours?: number;
  actual_hours?: number;
  progress_percentage: number;
  dependencies?: string[];
  tags?: string[];
  attachments?: any;
  is_milestone: boolean;
  created_at: string;
  updated_at: string;
}

export interface Milestone {
  id: string;
  phase_id: string;
  name: string;
  description?: string;
  target_date: string;
  achieved_date?: string;
  status: 'pending' | 'achieved' | 'missed' | 'cancelled';
  criteria?: string[];
  deliverables?: string[];
  is_critical: boolean;
  created_at: string;
  updated_at: string;
}

export interface RoadmapStats {
  total_phases: number;
  completed_phases: number;
  in_progress_phases: number;
  total_tasks: number;
  completed_tasks: number;
  total_milestones: number;
  achieved_milestones: number;
  overall_progress: number;
}

class RoadmapService {
  private async callEdgeFunction(action: string, params: any = {}) {
    const { data: { session } } = await supabase.auth.getSession();
    
    if (!session) {
      throw new Error('Not authenticated');
    }

    const { data, error } = await supabase.functions.invoke('roadmap_manager_2026_04_09', {
      body: { action, ...params },
      headers: {
        Authorization: `Bearer ${session.access_token}`,
      },
    });

    if (error) throw error;
    if (!data.success) throw new Error(data.error || 'Operation failed');

    return data;
  }

  async listPhases(): Promise<Phase[]> {
    const data = await this.callEdgeFunction('list_phases');
    return data.phases;
  }

  async getPhase(phase_id: string): Promise<{ phase: Phase; tasks: Task[]; milestones: Milestone[] }> {
    const data = await this.callEdgeFunction('get_phase', { phase_id });
    return {
      phase: data.phase,
      tasks: data.tasks,
      milestones: data.milestones
    };
  }

  async updatePhase(phase_id: string, updates: Partial<Phase>): Promise<Phase> {
    const data = await this.callEdgeFunction('update_phase', { phase_id, updates });
    return data.phase;
  }

  async listTasks(phase_id?: string): Promise<Task[]> {
    const data = await this.callEdgeFunction('list_tasks', { phase_id });
    return data.tasks;
  }

  async createTask(task: Partial<Task>): Promise<Task> {
    const data = await this.callEdgeFunction('create_task', task);
    return data.task;
  }

  async updateTask(task_id: string, updates: Partial<Task>): Promise<Task> {
    const data = await this.callEdgeFunction('update_task', { task_id, updates });
    return data.task;
  }

  async deleteTask(task_id: string): Promise<void> {
    await this.callEdgeFunction('delete_task', { task_id });
  }

  async listMilestones(phase_id?: string): Promise<Milestone[]> {
    const data = await this.callEdgeFunction('list_milestones', { phase_id });
    return data.milestones;
  }

  async updateMilestone(milestone_id: string, updates: Partial<Milestone>): Promise<Milestone> {
    const data = await this.callEdgeFunction('update_milestone', { milestone_id, updates });
    return data.milestone;
  }

  async getStats(): Promise<RoadmapStats> {
    const data = await this.callEdgeFunction('get_stats');
    return data.stats;
  }
}

export const roadmapService = new RoadmapService();
