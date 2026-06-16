import { useEffect, useState } from 'react';
import { api, type MatchRecord, type MatchStats } from '../api';
import { ChevronLeft, ChevronRight, Swords } from 'lucide-react';

export default function Matches() {
  const [matches, setMatches] = useState<MatchRecord[]>([]);
  const [stats, setStats] = useState<MatchStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [domain, setDomain] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const limit = 20;

  useEffect(() => {
    api.matchStats().then(setStats).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    api
      .matches(limit, offset, domain || undefined)
      .then((r) => setMatches(r.matches))
      .finally(() => setLoading(false));
  }, [offset, domain]);

  const domains = stats ? Object.keys(stats.by_domain) : [];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">比赛历史</h1>

      {stats && (
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm text-center">
            <div className="text-2xl font-bold text-gray-900">{stats.total}</div>
            <div className="text-sm text-gray-500">比赛总数</div>
          </div>
          <div className="bg-green-50 rounded-xl border border-green-200 p-4 text-center">
            <div className="text-2xl font-bold text-green-700">{stats.by_winner.A}</div>
            <div className="text-sm text-green-600">A 方胜</div>
          </div>
          <div className="bg-red-50 rounded-xl border border-red-200 p-4 text-center">
            <div className="text-2xl font-bold text-red-700">{stats.by_winner.B}</div>
            <div className="text-sm text-red-600">B 方胜</div>
          </div>
          <div className="bg-gray-50 rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-gray-700">{stats.by_winner.tie}</div>
            <div className="text-sm text-gray-500">平局</div>
          </div>
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => setDomain('')}
          className={`px-3 py-1.5 rounded-lg text-sm ${!domain ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600'}`}
        >
          全部
        </button>
        {domains.map((d) => (
          <button
            key={d}
            onClick={() => { setDomain(d); setOffset(0); }}
            className={`px-3 py-1.5 rounded-lg text-sm capitalize ${domain === d ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600'}`}
          >
            {d}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-center text-gray-500 p-8">加载中...</div>
      ) : (
        <div className="space-y-2">
          {matches.map((m) => (
            <div key={m.match_id} className="bg-white rounded-xl border border-gray-200 shadow-sm">
              <button
                onClick={() => setExpanded(expanded === m.match_id ? null : m.match_id)}
                className="w-full px-5 py-3 flex items-center gap-3 text-left"
              >
                <Swords className="w-4 h-4 text-gray-400" />
                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs capitalize">
                  {m.domain}
                </span>
                <span className="font-mono text-sm text-gray-700">{m.skill_a}</span>
                <span className="text-gray-400 text-sm">vs</span>
                <span className="font-mono text-sm text-gray-700">{m.skill_b}</span>
                <span
                  className={`ml-auto px-2 py-0.5 rounded text-xs font-bold ${
                    m.verdict.winner === 'A'
                      ? 'bg-green-100 text-green-700'
                      : m.verdict.winner === 'B'
                        ? 'bg-red-100 text-red-700'
                        : 'bg-gray-100 text-gray-600'
                  }`}
                >
                  {m.verdict.winner === 'tie' ? '平局' : `${m.verdict.winner} 胜`}
                </span>
              </button>
              {expanded === m.match_id && (
                <div className="px-5 pb-4 border-t border-gray-100">
                  <div className="mt-3 text-sm text-gray-600">
                    <strong>任务：</strong> {m.task_prompt}
                  </div>
                  <div className="mt-2 text-sm text-gray-500 italic">{m.verdict.reasoning}</div>
                  <div className="mt-3 grid grid-cols-2 gap-4">
                    {(['A', 'B'] as const).map((side) => {
                      const s = m.verdict.scores[side];
                      const name = side === 'A' ? m.skill_a : m.skill_b;
                      const dims = [
                        ['正确性', s.correctness],
                        ['完整性', s.completeness],
                        ['清晰度', s.clarity],
                        ['创造力', s.creativity],
                      ] as const;
                      return (
                        <div key={side} className="bg-gray-50 rounded-lg p-3">
                          <div className="font-mono text-sm font-medium text-gray-800 mb-2">
                            {name}
                          </div>
                          {dims.map(([label, val]) => (
                            <div key={label} className="flex items-center gap-2 text-xs mb-1">
                              <span className="text-gray-500 w-24">{label}</span>
                              <div className="flex-1 bg-gray-200 rounded-full h-1.5">
                                <div
                                  className={`h-1.5 rounded-full ${side === 'A' ? 'bg-green-500' : 'bg-blue-500'}`}
                                  style={{ width: `${val * 10}%` }}
                                />
                              </div>
                              <span className="text-gray-700 w-6 text-right">{val}</span>
                            </div>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-between items-center">
        <button
          onClick={() => setOffset(Math.max(0, offset - limit))}
          disabled={offset === 0}
          className="flex items-center gap-1 px-4 py-2 rounded-lg bg-gray-100 text-sm disabled:opacity-40"
        >
          <ChevronLeft className="w-4 h-4" /> 上一页
        </button>
        <span className="text-sm text-gray-500">
          {offset + 1}-{offset + matches.length}
        </span>
        <button
          onClick={() => setOffset(offset + limit)}
          disabled={matches.length < limit}
          className="flex items-center gap-1 px-4 py-2 rounded-lg bg-gray-100 text-sm disabled:opacity-40"
        >
          下一页 <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
