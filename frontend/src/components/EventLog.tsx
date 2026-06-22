import { useEffect, useRef } from 'react';
import type { SSEEvent } from '../hooks/useArenaStatus';

interface Props {
  events: SSEEvent[];
  maxLines?: number;
}

const WARN_TYPES = new Set([
  'phase_a_domain_skip',
  'phase_b_skip',
  'phase_c_skip',
  'phase_c_skip_cached',
  'phase_b_fuse_failed',
]);
const ERROR_TYPES = new Set(['cycle_error', 'run_error']);

const TYPE_COLOR: Record<string, string> = {
  phase_a_match: 'text-green-400',
  phase_a_skill_exec: 'text-green-300',
  phase_b_fuse_done: 'text-amber-400',
  phase_c_iteration: 'text-red-400',
  phase_c_improve_done: 'text-red-400',
  skill_output_start: 'text-blue-400',
  skill_output_chunk: 'text-blue-300',
  skill_output_done: 'text-blue-500',
};

function colorForType(t: string): string {
  if (ERROR_TYPES.has(t)) return 'text-red-400';
  if (WARN_TYPES.has(t)) return 'text-amber-400';
  if (TYPE_COLOR[t]) return TYPE_COLOR[t];
  return 'text-gray-300';
}

function formatEvent(evt: SSEEvent): string {
  const t = evt.type;
  const fields: string[] = [];
  switch (t) {
    case 'phase_a_match':
      fields.push(`MATCH ${evt.match_id}`);
      fields.push(`${evt.skill_a} vs ${evt.skill_b}`);
      fields.push(`→ ${evt.winner}`);
      fields.push(`${((evt.score_a as number) ?? 0).toFixed(1)}:${((evt.score_b as number) ?? 0).toFixed(1)}`);
      break;
    case 'phase_a_skill_exec':
      fields.push(`EXEC ${evt.skill} on ${evt.task_id}`);
      break;
    case 'phase_a_domain_start':
      fields.push(`DOMAIN ${evt.domain} started`);
      fields.push(`${evt.skill_count} skills, ${evt.domain_total_matches} matches`);
      break;
    case 'phase_a_domain_done':
      fields.push(`DOMAIN ${evt.domain} done`);
      break;
    case 'phase_a_plan':
      fields.push(`PLAN: ${evt.total_matches} total matches`);
      break;
    case 'phase_b_fuse_start':
      fields.push(`FUSE ${evt.skill_a} + ${evt.skill_b}`);
      break;
    case 'phase_b_fuse_done':
      fields.push(`FUSE DONE: ${evt.output_path}`);
      break;
    case 'phase_c_iteration':
      fields.push(`ITER ${evt.iteration} ${evt.skill} Elo ${((evt.elo_after as number) ?? 0).toFixed(0)} (Δ ${((evt.elo_delta as number) ?? 0).toFixed(1)})`);
      break;
    case 'phase_c_improve_done':
      fields.push(`IMPROVE DONE ${evt.skill} iters=${evt.total_iterations} converged=${evt.converged}`);
      break;
    case 'phase_start':
      fields.push(`PHASE ${evt.phase} started`);
      break;
    case 'phase_done':
      fields.push(`PHASE ${evt.phase} done`);
      break;
    case 'cycle_complete':
      fields.push(`CYCLE COMPLETE matches=${evt.match_count}`);
      break;
    case 'cycle_error':
      fields.push(`ERROR: ${evt.error}`);
      break;
    case 'cycle_start':
      fields.push(`CYCLE START skills=${evt.skill_count} tasks=${evt.task_count}`);
      break;
    case 'phase_a_domain_skip':
      fields.push(`DOMAIN ${evt.domain} SKIP (skills=${evt.skill_count})`);
      break;
    case 'phase_b_skip':
    case 'phase_c_skip':
      fields.push(`PHASE ${evt.type.includes('b') ? 'B' : 'C'} SKIP ${evt.domain}: ${evt.reason}`);
      break;
    case 'phase_c_skip_cached':
      fields.push(`PHASE C SKIP CACHED ${evt.skill}`);
      break;
    case 'phase_b_fuse_failed':
      fields.push(`FUSE FAILED ${evt.domain}: ${evt.error}`);
      break;
    case 'phase_c_improve_start':
      fields.push(`IMPROVE START ${evt.skill} max_iters=${evt.max_iterations}`);
      break;
    case 'run_start':
      fields.push(`RUN ${evt.run_id} start`);
      break;
    case 'run_end':
      fields.push(`RUN ${evt.run_id} end`);
      break;
    case 'skill_output_start':
      fields.push(`OUTPUT START ${evt.skill} on ${evt.task_id}`);
      break;
    case 'skill_output_chunk':
      // skip chunk events in log — too noisy; the viewer component handles display
      return '';
    case 'skill_output_done':
      fields.push(`OUTPUT DONE ${evt.skill} on ${evt.task_id} (${evt.tokens ?? '?'} tok)`);
      fields.push(evt.cache_hit ? '[cache]' : '[api]');
      break;
    default:
      fields.push(JSON.stringify(evt));
  }
  return fields.join(' ');
}

export default function EventLog({ events, maxLines = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const visible = events.slice(-maxLines);

  useEffect(() => {
    if (ref.current) {
      const { scrollTop, scrollHeight, clientHeight } = ref.current;
      // 只在用户接近底部时才自动滚动（50px 阈值）
      if (scrollHeight - scrollTop - clientHeight < 50) {
        ref.current.scrollTop = scrollHeight;
      }
    }
  }, [events]);

  // Filter out events with no display text (e.g. chunk events)
  const displayEvents = visible.filter((evt) => formatEvent(evt) !== '');

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-gray-900 font-semibold text-sm">
          事件日志 <span className="text-gray-400 text-xs font-normal">({displayEvents.length})</span>
        </h2>
        <div className="flex gap-3 text-[10px]">
          <span className="text-gray-400">● INFO</span>
          <span className="text-amber-400">● WARN</span>
          <span className="text-red-400">● ERR</span>
        </div>
      </div>
      <div
        ref={ref}
        className="bg-gray-900 rounded-lg p-3 h-48 overflow-auto text-xs leading-relaxed"
      >
        {displayEvents.length === 0 ? (
          <div className="text-gray-500">等待事件中...</div>
        ) : (
          displayEvents.map((evt, i) => {
            const ts = (evt.ts as string) || '';
            const time = ts.includes('T') ? ts.split('T')[1]?.slice(0, 8) : '';
            return (
              <div key={i} className="flex gap-2 hover:bg-white/5 px-1 -mx-1">
                <span className="text-gray-500 flex-shrink-0">{time}</span>
                <span className={`flex-shrink-0 ${colorForType(evt.type)}`}>[{evt.type}]</span>
                <span className="text-gray-300 truncate">{formatEvent(evt)}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
