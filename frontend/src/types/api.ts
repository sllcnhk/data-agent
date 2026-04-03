export interface Agent {
  agent_id: string;
  agent_type: string;
  name: string;
  description: string;
  version: string;
  status: string;
  created_at: string;
  last_active_at: string;
  capabilities: string[];
  current_task?: Task;
  completed_tasks: number;
  failed_tasks: number;
}

export interface Task {
  task_id: string;
  agent_type: string;
  priority: number;
  status: string;
  input_data: Record<string, any>;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  error?: string;
  retry_count: number;
}

export interface Skill {
  name: string;
  description: string;
  skill_type: string;
  schema: Record<string, any>;
}

export interface SystemHealth {
  status: string;
  total_agents: number;
  agent_types: Record<string, number>;
  queue_size: number;
  active_workers: number;
  agents: Array<{
    agent_id: string;
    status: string;
    current_task?: string;
  }>;
}

export interface TaskSubmitRequest {
  query: string;
  priority?: string;
  context?: Record<string, any>;
}

export interface TaskSubmitResponse {
  success: boolean;
  task_id: string;
  agent_type: string;
  priority: number;
  message: string;
}

export interface TaskStatus {
  task_id: string;
  status: string;
  agent_type?: string;
  priority: number;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  error?: string;
}

export interface AgentMetrics {
  agent_id: string;
  info: Agent;
  metrics: {
    total_tasks: number;
    completed_tasks: number;
    failed_tasks: number;
  };
}

export interface RoutingSuggestion {
  agent_type: string;
  priority: number;
  confidence: number;
  keywords: string[];
}

export interface SkillExecutionResult {
  name: string;
  success: boolean;
  data?: Record<string, any>;
  error?: string;
}
