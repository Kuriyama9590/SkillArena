import type { LiveBattle, LatestResult } from '../hooks/useArenaStatus';

interface Props {
  liveBattle: LiveBattle | null;
  latestResult: LatestResult | null;
  matchIndex: number;
  totalMatches: number;
}

export default function BattleArena({ liveBattle, latestResult, matchIndex, totalMatches }: Props) {
  const a = latestResult?.skill_a ?? liveBattle?.skill_a ?? null;
  const b = latestResult?.skill_b ?? liveBattle?.skill_b ?? null;
  const winner = latestResult?.winner;
  const scoreA = latestResult?.score_a;
  const scoreB = latestResult?.score_b;
  const eloA = latestResult?.elo_a;
  const eloB = latestResult?.elo_b;

  const progressPct = totalMatches > 0 ? (matchIndex / totalMatches) * 100 : 0;

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-gray-900 font-semibold text-sm">对战现场</h2>
        <div className="text-xs text-gray-500">
          <span className="text-green-600 font-semibold">{matchIndex}</span>
          <span className="text-gray-300"> / </span>
          <span>{totalMatches || '?'}</span>
          <span className="text-gray-300 ml-2">MATCHES</span>
        </div>
      </div>

      {/* 进度条 */}
      <div className="mb-5">
        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-green-500 transition-all duration-500"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <div className="flex justify-between mt-1 text-xs text-gray-400">
          <span>0%</span>
          <span className="text-green-600">{progressPct.toFixed(1)}%</span>
          <span>100%</span>
        </div>
      </div>

      {/* 对战区 */}
      <div className="grid grid-cols-[1fr_auto_1fr] gap-3 items-center min-h-[200px]">
        <FighterCard
          name={a}
          side="A"
          score={scoreA}
          elo={eloA}
          isWinner={winner === 'A'}
          isDraw={winner === 'tie'}
          active={!!liveBattle && !latestResult}
        />
        {/* VS */}
        <div className="flex flex-col items-center gap-2 px-2">
          <div
            className={`text-3xl font-bold ${
              winner ? 'text-red-500' : 'text-gray-400 animate-pulse'
            }`}
          >
            {winner ? (winner === 'tie' ? 'TIE' : 'VS') : 'VS'}
          </div>
          {winner && winner !== 'tie' && (
            <div className="text-xs text-green-600 font-semibold">
              ⚡ WIN ⚡
            </div>
          )}
        </div>
        <FighterCard
          name={b}
          side="B"
          score={scoreB}
          elo={eloB}
          isWinner={winner === 'B'}
          isDraw={winner === 'tie'}
          active={!!liveBattle && !latestResult}
        />
      </div>

      {!a && !b && (
        <div className="text-center text-gray-400 py-8">
          等待对战开始...
        </div>
      )}
    </div>
  );
}

function FighterCard({
  name,
  side,
  score,
  elo,
  isWinner,
  isDraw,
  active,
}: {
  name: string | null;
  side: 'A' | 'B';
  score?: number;
  elo?: number;
  isWinner?: boolean;
  isDraw?: boolean;
  active?: boolean;
}) {
  return (
    <div
      className={`relative rounded-lg border-2 p-4 transition-all ${
        isWinner
          ? 'border-green-300 bg-green-50'
          : isDraw
            ? 'border-gray-300 bg-gray-50'
            : active
              ? 'border-blue-300 bg-blue-50 animate-pulse'
              : 'border-gray-200 bg-gray-50'
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span
          className={`text-xs ${
            isWinner ? 'text-green-700 font-semibold' : 'text-gray-500'
          }`}
        >
          FIGHTER {side}
        </span>
        {isWinner && (
          <span className="text-xs text-green-700 bg-green-100 px-1.5 py-0.5 rounded">
            WINNER
          </span>
        )}
      </div>
      <div
        className={`text-base font-bold truncate ${
          isWinner ? 'text-green-700' : 'text-gray-900'
        }`}
        title={name || ''}
      >
        {name || '—'}
      </div>
      {score !== undefined && (
        <div className="mt-2 flex items-baseline gap-2">
          <span
            className={`text-3xl font-bold ${
              isWinner ? 'text-green-700' : 'text-gray-900'
            }`}
          >
            {score.toFixed(1)}
          </span>
          {elo !== undefined && (
            <span className="text-xs text-gray-500">
              ELO {elo.toFixed(0)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
