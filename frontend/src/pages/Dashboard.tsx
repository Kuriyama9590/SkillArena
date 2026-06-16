import { useEffect, useState } from 'react';
import { api, type DashboardData } from '../api';
import { Trophy, Swords, Users, Zap } from 'lucide-react';

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.dashboard().then(setData).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-center text-gray-500">加载中...</div>;
  if (!data) return <div className="p-8 text-center text-red-500">仪表盘加载失败</div>;

  const cards = [
    {
      label: '技能总数',
      value: data.total_skills,
      icon: <Users className="w-5 h-5" />,
      color: 'bg-blue-500',
    },
    {
      label: '比赛总数',
      value: data.total_matches,
      icon: <Swords className="w-5 h-5" />,
      color: 'bg-green-500',
    },
    {
      label: '领域数',
      value: Object.keys(data.domains).length,
      icon: <Zap className="w-5 h-5" />,
      color: 'bg-purple-500',
    },
    {
      label: '冠军技能',
      value: Object.values(data.domain_top)[0]?.name || '-',
      icon: <Trophy className="w-5 h-5" />,
      color: 'bg-amber-500',
      sub: Object.values(data.domain_top)[0]
        ? `Elo ${Object.values(data.domain_top)[0].elo.toFixed(0)}`
        : undefined,
    },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">仪表盘</h1>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map((c) => (
          <div key={c.label} className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
            <div className="flex items-center gap-3 mb-3">
              <div className={`${c.color} text-white p-2 rounded-lg`}>{c.icon}</div>
              <span className="text-sm text-gray-500">{c.label}</span>
            </div>
            <div className="text-2xl font-bold text-gray-900">{c.value}</div>
            {c.sub && <div className="text-sm text-gray-500 mt-1">{c.sub}</div>}
          </div>
        ))}
      </div>

      {Object.keys(data.domain_top).length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">各领域冠军</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {Object.entries(data.domain_top).map(([domain, info]) => (
              <div key={domain} className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
                <Trophy className="w-5 h-5 text-amber-500" />
                <div>
                  <div className="text-sm font-medium text-gray-700 capitalize">{domain}</div>
                  <div className="text-lg font-bold text-gray-900">{info.name}</div>
                  <div className="text-xs text-gray-500">Elo {info.elo.toFixed(1)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.recent_matches.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">最近比赛</h2>
          <div className="space-y-2">
            {data.recent_matches.map((m) => (
              <div
                key={m.match_id}
                className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg text-sm"
              >
                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs capitalize">
                  {m.domain}
                </span>
                <span className="font-mono text-gray-600">{m.skill_a}</span>
                <span className="text-gray-400">vs</span>
                <span className="font-mono text-gray-600">{m.skill_b}</span>
                <span
                  className={`ml-auto px-2 py-0.5 rounded text-xs font-bold ${
                    m.verdict.winner === 'A'
                      ? 'bg-green-100 text-green-700'
                      : m.verdict.winner === 'B'
                        ? 'bg-red-100 text-red-700'
                        : 'bg-gray-100 text-gray-700'
                  }`}
                >
                  {m.verdict.winner === 'A' ? m.skill_a : m.verdict.winner === 'B' ? m.skill_b : '平局'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
