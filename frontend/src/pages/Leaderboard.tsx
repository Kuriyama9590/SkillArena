import { useEffect, useState } from 'react';
import { api, type EloData } from '../api';
import { Trophy, TrendingUp } from 'lucide-react';

const DOMAIN_COLORS: Record<string, string> = {
  writing: 'bg-blue-500',
  coding: 'bg-green-500',
  analysis: 'bg-purple-500',
  general: 'bg-gray-500',
};

export default function Leaderboard() {
  const [elo, setElo] = useState<EloData | null>(null);
  const [activeDomain, setActiveDomain] = useState<string>('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.elo().then(setElo).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (elo && !activeDomain) {
      const keys = Object.keys(elo);
      if (keys.length > 0) setActiveDomain(keys[0]);
    }
  }, [elo, activeDomain]);

  if (loading) return <div className="p-8 text-center text-gray-500">加载中...</div>;
  if (!elo || Object.keys(elo).length === 0)
    return <div className="p-8 text-center text-gray-500">暂无 Elo 数据，请先运行竞技！</div>;

  const domains = Object.keys(elo);
  const currentRatings = elo[activeDomain] || {};
  const sorted = Object.entries(currentRatings)
    .filter(([name]) => !name.startsWith('baseline'))
    .sort(([, a], [, b]) => b - a);
  const maxElo = Math.max(...sorted.map(([, r]) => r), 1500);
  const minElo = Math.min(...sorted.map(([, r]) => r), 1400);
  const range = maxElo - minElo || 1;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">排行榜</h1>

      <div className="flex gap-2">
        {domains.map((d) => (
          <button
            key={d}
            onClick={() => setActiveDomain(d)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors capitalize ${
              activeDomain === d
                ? 'bg-gray-900 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {d}
          </button>
        ))}
      </div>

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100">
              <th className="text-left text-xs font-medium text-gray-500 uppercase px-5 py-3">排名</th>
              <th className="text-left text-xs font-medium text-gray-500 uppercase px-5 py-3">技能</th>
              <th className="text-left text-xs font-medium text-gray-500 uppercase px-5 py-3">Elo</th>
              <th className="text-left text-xs font-medium text-gray-500 uppercase px-5 py-3 w-48">进度条</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(([name, rating], i) => {
              const pct = ((rating - minElo) / range) * 100;
              return (
                <tr key={name} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      {i === 0 ? (
                        <Trophy className="w-4 h-4 text-amber-500" />
                      ) : i === 1 ? (
                        <Trophy className="w-4 h-4 text-gray-400" />
                      ) : i === 2 ? (
                        <Trophy className="w-4 h-4 text-amber-700" />
                      ) : null}
                      <span className="text-sm font-medium text-gray-900">{i + 1}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm text-gray-900">{name}</span>
                      {i === 0 && (
                        <TrendingUp className="w-3.5 h-3.5 text-green-500" />
                      )}
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <span className="font-mono text-sm font-bold text-gray-900">
                      {rating.toFixed(1)}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    <div className="w-full bg-gray-100 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full ${DOMAIN_COLORS[activeDomain] || 'bg-gray-500'}`}
                        style={{ width: `${Math.max(pct, 5)}%` }}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
