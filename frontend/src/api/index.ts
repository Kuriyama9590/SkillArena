const API_BASE = import.meta.env.VITE_API_URL || '';

async function fetchAPI<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}

export interface SkillInfo {
  name: string;
  filename: string;
  path: string;
  domains: string[];
  content_length: number;
  preview: string;
}

export interface SkillDetail {
  name: string;
  content: string;
}

export interface EloData {
  [domain: string]: { [skill: string]: number };
}

export interface MatchRecord {
  match_id: string;
  timestamp: string;
  task_id: string;
  task_prompt: string;
  skill_a: string;
  skill_b: string;
  verdict: {
    winner: string;
    scores: {
      A: { correctness: number; completeness: number; clarity: number; creativity: number };
      B: { correctness: number; completeness: number; clarity: number; creativity: number };
    };
    reasoning: string;
  };
  output_a: string;
  output_b: string;
  domain: string;
}

export interface MatchStats {
  total: number;
  by_domain: Record<string, number>;
  by_winner: { A: number; B: number; tie: number };
}

export interface TaskFile {
  filename: string;
  category: string;
  task_count: number;
  tasks: Array<{
    id: string;
    category: string;
    prompt: string;
    difficulty: string;
    [key: string]: unknown;
  }>;
}

export interface ReportInfo {
  filename: string;
  size: number;
  modified: number;
}

export interface DashboardData {
  total_skills: number;
  total_matches: number;
  domains: Record<string, number>;
  domain_top: Record<string, { name: string; elo: number }>;
  recent_matches: MatchRecord[];
}

export interface ArenaStatus {
  running: boolean;
  phase: string | null;
  domain: string | null;
  match_index: number;
  total_matches: number;
  latest_result: {
    domain?: string;
    skill_a?: string;
    skill_b?: string;
    winner?: string;
    score_a?: number;
    score_b?: number;
    elo_a?: number;
    elo_b?: number;
  } | null;
  current_battle: {
    skill_a?: string;
    skill_b?: string;
    domain?: string;
    match_id?: string;
  } | null;
  elo_snapshot: Record<string, number>;
  current_run_file: string | null;
  phases: Record<string, string>;
}

export const api = {
  dashboard: () => fetchAPI<DashboardData>('/api/dashboard'),
  skills: () => fetchAPI<SkillInfo[]>('/api/skills'),
  skill: (name: string) => fetchAPI<SkillDetail>(`/api/skills/${name}`),
  uploadSkill: (name: string, content: string) =>
    fetchAPI<{ name: string; created: boolean }>('/api/skills/upload', {
      method: 'POST',
      body: JSON.stringify({ name, content }),
    }),
  updateSkill: (name: string, content: string) =>
    fetchAPI<{ name: string; updated: boolean }>(`/api/skills/${name}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    }),
  deleteSkill: (name: string) =>
    fetchAPI<{ name: string; deleted: boolean }>(`/api/skills/${name}`, {
      method: 'DELETE',
    }),
  elo: () => fetchAPI<EloData>('/api/elo'),
  eloDomain: (domain: string) =>
    fetchAPI<{ domain: string; leaderboard: [string, number][] }>(`/api/elo/${domain}`),
  matches: (limit = 50, offset = 0, domain?: string) =>
    fetchAPI<{ matches: MatchRecord[] }>(
      `/api/matches?limit=${limit}&offset=${offset}${domain ? `&domain=${domain}` : ''}`,
    ),
  matchStats: () => fetchAPI<MatchStats>('/api/matches/stats'),
  tasks: () => fetchAPI<TaskFile[]>('/api/tasks'),
  reports: () => fetchAPI<ReportInfo[]>('/api/reports'),
  report: (filename: string) =>
    fetchAPI<{ filename: string; content: string }>(`/api/reports/${filename}`),
  arenaStatus: () => fetchAPI<ArenaStatus>('/api/arena/status'),
  arenaRun: (opts?: {
    skills?: string[];
    task_source?: string;
    rounds_per_pair?: number;
    max_improve_iterations?: number;
    run_fusion?: boolean;
    run_improvement?: boolean;
    auto_categories?: string[];
    auto_per_category?: number;
  }) =>
    fetchAPI<{ status: string; run_id: string; event_file: string }>('/api/arena/run', {
      method: 'POST',
      body: JSON.stringify(opts || {}),
    }),
  arenaPhaseA: (opts?: { skills?: string[]; task_source?: string; rounds_per_pair?: number }) =>
    fetchAPI<{ status: string }>('/api/arena/phase-a', {
      method: 'POST',
      body: JSON.stringify(opts || {}),
    }),
  arenaReset: () =>
    fetchAPI<{ status: string }>('/api/arena/state', { method: 'DELETE' }),
  arenaRuns: () => fetchAPI<Array<{ filename: string; run_id: string; size: number; modified: number }>>(
    '/api/arena/runs',
  ),
  arenaRunEvents: (filename: string) =>
    fetchAPI<{ filename: string; event_count: number; events: any[] }>(
      `/api/arena/runs/${filename}`,
    ),
};
