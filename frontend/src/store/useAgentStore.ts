import { create } from 'zustand';
import { Agent, Task, SystemHealth } from '@/types/api';

interface AgentState {
  agents: Agent[];
  tasks: Task[];
  systemHealth: SystemHealth | null;
  loading: boolean;
  error: string | null;

  // Actions
  setAgents: (agents: Agent[]) => void;
  setTasks: (tasks: Task[]) => void;
  setSystemHealth: (health: SystemHealth) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  addTask: (task: Task) => void;
  updateTask: (taskId: string, updates: Partial<Task>) => void;
  addAgent: (agent: Agent) => void;
  removeAgent: (agentId: string) => void;
}

export const useAgentStore = create<AgentState>((set) => ({
  agents: [],
  tasks: [],
  systemHealth: null,
  loading: false,
  error: null,

  setAgents: (agents) => set({ agents }),

  setTasks: (tasks) => set({ tasks }),

  setSystemHealth: (health) => set({ systemHealth: health }),

  setLoading: (loading) => set({ loading }),

  setError: (error) => set({ error }),

  addTask: (task) => set((state) => ({
    tasks: [task, ...state.tasks]
  })),

  updateTask: (taskId, updates) => set((state) => ({
    tasks: state.tasks.map((task) =>
      task.task_id === taskId ? { ...task, ...updates } : task
    )
  })),

  addAgent: (agent) => set((state) => ({
    agents: [...state.agents, agent]
  })),

  removeAgent: (agentId) => set((state) => ({
    agents: state.agents.filter((agent) => agent.agent_id !== agentId)
  })),
}));
