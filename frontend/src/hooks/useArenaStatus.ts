import { useState, useEffect, useRef, useCallback } from 'react';
import { api, type ArenaStatus } from '../api';
import type { SkillOutput } from '../components/SkillOutputViewer';

export interface SSEEvent {
  type: string;
  ts?: string;
  [key: string]: unknown;
}

export interface LiveBattle {
  skill_a: string;
  skill_b?: string;
  domain?: string;
  match_id?: string;
}

export interface LatestResult {
  domain?: string;
  skill_a?: string;
  skill_b?: string;
  winner?: string;
  score_a?: number;
  score_b?: number;
  elo_a?: number;
  elo_b?: number;
}

export interface UseArenaStatus {
  status: ArenaStatus;
  events: SSEEvent[];
  connected: boolean;
  isReplaying: boolean;
  replayRunId: string | null;
  liveBattle: LiveBattle | null;
  latestResult: LatestResult | null;
  skillOutputs: Map<string, SkillOutput>;
  loadReplay: (filename: string) => Promise<void>;
  clearReplay: () => void;
}

export function useArenaStatus(): UseArenaStatus {
  const [status, setStatus] = useState<ArenaStatus>({
    running: false,
    phase: null,
    domain: null,
    match_index: 0,
    total_matches: 0,
    latest_result: null,
    current_battle: null,
    elo_snapshot: {},
    current_run_file: null,
    phases: {},
  });
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [liveBattle, setLiveBattle] = useState<LiveBattle | null>(null);
  const [latestResult, setLatestResult] = useState<LatestResult | null>(null);
  const [isReplaying, setIsReplaying] = useState(false);
  const [replayRunId, setReplayRunId] = useState<string | null>(null);
  const [skillOutputs, setSkillOutputs] = useState<Map<string, SkillOutput>>(new Map());
  const reconnectCount = useRef(0);

  // 轮询状态(若正在回放则暂停)
  useEffect(() => {
    if (isReplaying) return;
    const poll = setInterval(() => {
      api.arenaStatus().then(setStatus).catch(() => {});
    }, 3000);
    api.arenaStatus().then(setStatus).catch(() => {});
    return () => clearInterval(poll);
  }, [isReplaying]);

  // 处理单个事件,更新派生状态
  const handleEvent = useCallback((evt: SSEEvent) => {
    setEvents((prev) => [...prev.slice(-200), evt]);
    const t = evt.type;
    if (t === 'phase_a_match') {
      setLiveBattle({
        skill_a: evt.skill_a as string,
        skill_b: evt.skill_b as string,
        domain: evt.domain as string,
        match_id: evt.match_id as string,
      });
      setLatestResult({
        domain: evt.domain as string,
        skill_a: evt.skill_a as string,
        skill_b: evt.skill_b as string,
        winner: evt.winner as string,
        score_a: evt.score_a as number,
        score_b: evt.score_b as number,
        elo_a: evt.elo_a as number,
        elo_b: evt.elo_b as number,
      });
    } else if (t === 'phase_a_skill_exec') {
      setLiveBattle({
        skill_a: evt.skill as string,
        domain: evt.domain as string,
      });
    } else if (t === 'skill_output_start') {
      const key = `${evt.task_id}__${evt.skill}`;
      setSkillOutputs((prev) => {
        const next = new Map(prev);
        next.set(key, {
          skill: evt.skill as string,
          task_id: evt.task_id as string,
          domain: evt.domain as string,
          output: '',
          done: false,
        });
        return next;
      });
    } else if (t === 'skill_output_chunk') {
      const key = `${evt.task_id}__${evt.skill}`;
      setSkillOutputs((prev) => {
        const next = new Map(prev);
        const existing = next.get(key);
        if (existing) {
          next.set(key, { ...existing, output: (evt.accumulated as string) ?? existing.output + (evt.text as string) });
        }
        return next;
      });
    } else if (t === 'skill_output_done') {
      const key = `${evt.task_id}__${evt.skill}`;
      setSkillOutputs((prev) => {
        const next = new Map(prev);
        next.set(key, {
          skill: evt.skill as string,
          task_id: evt.task_id as string,
          domain: evt.domain as string,
          output: evt.output as string,
          tokens: evt.tokens as number | undefined,
          done: true,
        });
        return next;
      });
    } else if (t === 'cycle_complete' || t === 'cycle_error' || t === 'run_end') {
      setLiveBattle(null);
    } else if (t === 'run_start') {
      // 新运行开始:清空上一轮(hydration 回灌或上一场遗留)的事件/对战态
      setEvents([]);
      setLiveBattle(null);
      setLatestResult(null);
      setSkillOutputs(new Map());
    }
  }, []);

  // 挂载时:用最近一次运行的事件流恢复 events / liveBattle / latestResult
  // (页面刷新后竞技状态保留 —— status 由轮询恢复,但事件列表/对战态/最新结果只由 SSE 驱动)
  // 注意:必须放在 handleEvent 声明之后,否则依赖数组求值时触发 TDZ
  useEffect(() => {
    let aborted = false;
    api.arenaStatus().then((s) => {
      if (aborted || !s.current_run_file) return;
      api.arenaRunEvents(s.current_run_file).then(({ events: hist }) => {
        if (aborted || hist.length === 0) return;
        setEvents(hist.slice(-200));
        // 从最后一场 match 恢复 liveBattle / latestResult
        for (let i = hist.length - 1; i >= 0; i--) {
          if (hist[i].type === 'phase_a_match') {
            handleEvent(hist[i]);
            break;
          }
        }
      }).catch(() => {});
    }).catch(() => {});
    return () => { aborted = true; };
  }, [handleEvent]);

  // SSE 连接(回放时禁用)
  useEffect(() => {
    if (isReplaying) return;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let aborted = false;

    const connect = () => {
      if (aborted) return;
      const API_BASE = import.meta.env.VITE_API_URL || '';
      const url = `${API_BASE}/api/arena/events`;
      es = new EventSource(url);

      es.onopen = () => {
        reconnectCount.current = 0;
        setConnected(true);
      };

      es.onerror = () => {
        setConnected(false);
        es?.close();
        if (aborted) return;
        const delay = Math.min(1000 * 2 ** reconnectCount.current, 30000);
        reconnectCount.current += 1;
        reconnectTimer = setTimeout(connect, delay);
      };

      es.onmessage = (e) => {
        if (!e.data || e.data.startsWith(':')) return;
        try {
          const evt: SSEEvent = JSON.parse(e.data);
          handleEvent(evt);
        } catch {}
      };
    };

    connect();

    return () => {
      aborted = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
      setConnected(false);
    };
  }, [isReplaying, handleEvent]);

  // 加载历史运行(回放模式)
  const loadReplay = useCallback(async (filename: string) => {
    setIsReplaying(true);
    setEvents([]);
    setLiveBattle(null);
    setLatestResult(null);
    setSkillOutputs(new Map());
    setReplayRunId(filename);
    try {
      const { events: hist } = await api.arenaRunEvents(filename);
      // batch set
      setEvents(hist.slice(-200));
      // only update UI for last match event
      for (let i = hist.length - 1; i >= 0; i--) {
        if (hist[i].type === 'phase_a_match') {
          handleEvent(hist[i]);
          break;
        }
      }
    } catch (err) {
      console.error('loadReplay failed', err);
    }
  }, [handleEvent]);

  const clearReplay = useCallback(() => {
    setIsReplaying(false);
    setReplayRunId(null);
    setEvents([]);
    setLiveBattle(null);
    setLatestResult(null);
    setSkillOutputs(new Map());
    api.arenaStatus().then(setStatus).catch(() => {});
  }, []);

  return {
    status,
    events,
    connected,
    isReplaying,
    replayRunId,
    liveBattle,
    latestResult,
    skillOutputs,
    loadReplay,
    clearReplay,
  };
}
