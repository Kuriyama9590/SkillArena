import { useMemo } from 'react';

interface Props {
  eloSnapshot: Record<string, number>;
  latestEloChanges?: { name: string; elo: number; delta: number }[];
}

export default function EloLeaderboard({ eloSnapshot, latestEloChanges }: Props) {
  const ranked = useMemo(() => {
    return Object.entries(eloSnapshot)
      .filter(([n]) => !n.startsWith('baseline'))
      .sort(([na, a], [nb, b]) => b - a || na.localeCompare(nb));
  }, [eloSnapshot]);

  const changeMap = useMemo(() => {
    const m = new Map<string, number>();
    latestEloChanges?.forEach((c) => m.set(c.name, c.delta));
    return m;
  }, [latestEloChanges]);

  if (ranked.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <h2 className="text-gray-900 font-semibold text-sm mb-3">Elo 排行榜</h2>
        <div className="text-center text-gray-400 py-4">
          暂无 Elo 数据
        </div>
      </div>
    );
  }

  const maxElo = Math.max(...ranked.map(([, r]) => r));
  const minElo = Math.min(...ranked.map(([, r]) => r));
  const range = Math.max(maxElo - minElo, 1);

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <h2 className="text-gray-900 font-semibold text-sm mb-3">Elo 排行榜</h2>
      <div className="space-y-1.5">
        {ranked.map(([name, rating], i) => {
          const delta = changeMap.get(name);
          const pct = ((rating - minElo) / range) * 100;
          const isTop3 = i < 3;
          return (
            <div key={name} className="flex items-center gap-2 text-sm">
              <span
                className={`w-6 text-right ${
                  isTop3 ? 'text-amber-500 font-bold' : 'text-gray-400'
                }`}
              >
                {i === 0 ? '👑' : i === 1 ? '🥈' : i === 2 ? '🥉' : i + 1}
              </span>
              <div className="flex-1 min-w-0 flex items-center gap-2">
                <span
                  className={`truncate ${isTop3 ? 'text-gray-900' : 'text-gray-600'}`}
                  title={name}
                >
                  {name}
                </span>
                <div className="flex-1 h-2 bg-gray-100 rounded overflow-hidden">
                  <div
                    className="h-full bg-blue-500"
                    style={{ width: `${Math.max(pct, 5)}%` }}
                  />
                </div>
              </div>
              <span className="text-gray-900 font-semibold w-14 text-right">
                {rating.toFixed(0)}
              </span>
              {delta !== undefined && (
                <span
                  className={`w-14 text-right text-xs ${
                    delta > 0
                      ? 'text-green-600'
                      : delta < 0
                        ? 'text-red-600'
                        : 'text-gray-400'
                  }`}
                >
                  {delta > 0 ? '↑' : delta < 0 ? '↓' : '·'}
                  {Math.abs(delta).toFixed(1)}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
