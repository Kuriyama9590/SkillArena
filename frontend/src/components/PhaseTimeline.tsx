import type { ArenaStatus } from '../api';

interface Props {
  status: ArenaStatus;
  phase: string | null;
  domain: string | null;
}

const PHASES = [
  { key: 'A', label: 'A 竞技', desc: '两两对战', color: 'text-green-600' },
  { key: 'B', label: 'B 融合', desc: 'Top2 融合', color: 'text-amber-600' },
  { key: 'C', label: 'C 改进', desc: '自改进', color: 'text-red-600' },
  { key: 'D', label: 'D 报告', desc: '生成报告', color: 'text-purple-600' },
] as const;

export default function PhaseTimeline({ status, phase, domain }: Props) {
  const getPhaseStatus = (key: string): 'done' | 'running' | 'pending' | 'failed' => {
    const flatStatus = status.phases?.[key];
    if (flatStatus && flatStatus !== 'pending') {
      return flatStatus === 'done' ? 'done' : flatStatus === 'failed' ? 'failed' : 'running';
    }
    // 对于 B/C：检查所有领域化钥匙 B_writing B_coding 等
    const prefix = key + '_';
    let hasRunning = false;
    for (const [pkey, pval] of Object.entries(status.phases || {})) {
      if (pkey.startsWith(prefix)) {
        if (pval === 'done') return 'done';
        if (pval === 'running' || pval === 'done') hasRunning = true;
        if (pval === 'failed') return 'failed';
      }
    }
    if (hasRunning) return 'running';
    if (key === phase) return 'running';
    return 'pending';
  };

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-gray-900 font-semibold text-sm">比赛时间线</h2>
        {domain && (
          <span className="text-xs text-amber-600 bg-amber-50 px-2 py-0.5 border border-amber-200 rounded">
            DOMAIN: {domain.toUpperCase()}
          </span>
        )}
      </div>

      <div className="flex items-stretch gap-2">
        {PHASES.map((p, i) => {
          const s = getPhaseStatus(p.key);
          const isCurrent = s === 'running';
          const isDone = s === 'done';
          const isFailed = s === 'failed';

          return (
            <div key={p.key} className="flex-1 flex items-stretch">
              <div
                className={`flex-1 rounded-xl p-3 border transition-all ${
                  isCurrent
                    ? 'border-green-300 bg-green-50'
                    : isDone
                      ? 'border-green-200 bg-green-50/50'
                      : isFailed
                        ? 'border-red-300 bg-red-50'
                        : 'border-gray-200 bg-gray-50'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <div
                    className={`w-3 h-3 rounded-full ${
                      isCurrent
                        ? 'bg-green-500 animate-pulse'
                        : isDone
                          ? 'bg-green-500'
                          : isFailed
                            ? 'bg-red-500'
                            : 'bg-gray-300'
                    }`}
                  />
                  <span
                    className={`text-sm font-bold ${
                      isCurrent
                        ? 'text-green-700'
                        : isDone
                          ? 'text-green-600'
                          : isFailed
                            ? 'text-red-600'
                            : 'text-gray-400'
                    }`}
                  >
                    PHASE {p.key}
                  </span>
                </div>
                <div
                  className={`text-sm font-medium ${
                    isCurrent
                      ? 'text-gray-900'
                      : isDone
                        ? 'text-gray-700'
                        : 'text-gray-400'
                  }`}
                >
                  {p.label.replace(/^[A-D] /, '')}
                </div>
                <div
                  className={`text-xs mt-0.5 ${
                    isCurrent
                      ? 'text-green-600'
                      : isDone
                        ? 'text-green-500'
                        : 'text-gray-400'
                  }`}
                >
                  {s === 'done' ? '✓ COMPLETE' : s === 'running' ? '◉ IN PROGRESS' : s === 'failed' ? '✗ FAILED' : '○ PENDING'}
                </div>
              </div>
              {i < PHASES.length - 1 && (
                <div className="flex items-center px-1">
                  <div
                    className={`h-0.5 w-6 ${
                      s === 'done' || (isCurrent)
                        ? 'bg-green-400'
                        : 'bg-gray-200'
                    }`}
                  />
                  <div
                    className={s === 'done' ? 'text-green-400' : 'text-gray-300'}
                  >
                    ▶
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
