import { useEffect, useMemo, useState } from 'react';
import { api, type SkillInfo } from '../api';
import { useArenaStatus } from '../hooks/useArenaStatus';
import { Play, RotateCcw, Radio, Loader2, History, X } from 'lucide-react';
import PhaseTimeline from '../components/PhaseTimeline';
import BattleArena from '../components/BattleArena';
import EloLeaderboard from '../components/EloLeaderboard';
import EventLog from '../components/EventLog';
import SkillOutputViewer from '../components/SkillOutputViewer';

interface RunSummary {
  filename: string;
  run_id: string;
  size: number;
  modified: number;
}

// 专用领域(writing/coding/analysis)——这些领域之间互斥,不能同选
const SPECIFIC_DOMAINS = new Set(['writing', 'coding', 'analysis']);

const specificDomainsOf = (domains: string[]): string[] =>
  domains.filter((d) => SPECIFIC_DOMAINS.has(d));

export default function Arena() {
  const {
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
  } = useArenaStatus();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [taskSource, setTaskSource] = useState('fixed');
  const [rounds, setRounds] = useState(2);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [skills, setSkills] = useState<string[]>([]);
  const [allSkills, setAllSkills] = useState<SkillInfo[]>([]);
  const [maxIterations, setMaxIterations] = useState(2);
  const [runFusion, setRunFusion] = useState(true);
  const [runImprovement, setRunImprovement] = useState(true);
  const [autoCategories, setAutoCategories] = useState<string[]>(['writing', 'coding', 'analysis']);
  const [autoPerCategory, setAutoPerCategory] = useState(3);

  useEffect(() => {
    api.arenaRuns().then(setRuns).catch(() => {});
    api.skills().then(setAllSkills).catch(() => {});
  }, []);

  // 已选技能中的专用领域集合:用于跨域互斥约束
  // 规则:专用领域(writing/coding/analysis)之间不能同选,通用(general)除外
  const selectedSpecificDomains = useMemo(() => {
    const set = new Set<string>();
    for (const name of skills) {
      const info = allSkills.find((s) => s.name === name);
      if (!info) continue;
      for (const d of specificDomainsOf(info.domains ?? [])) set.add(d);
    }
    return set;
  }, [skills, allSkills]);

  // 锁定的领域:恰好选中一个专用领域时锁定它,其余专用领域禁选
  const lockedDomain =
    selectedSpecificDomains.size === 1 ? [...selectedSpecificDomains][0] : null;
  const hasDomainConflict = selectedSpecificDomains.size > 1;

  // 自动类目受锁定领域约束:选了专用技能后,auto 生成任务的类目强制为该领域
  const effectiveAutoCategories = lockedDomain ? [lockedDomain] : autoCategories;

  // 某 skill 是否可在当前锁定状态下被勾选
  const isSkillSelectable = (s: SkillInfo): boolean => {
    const domains = s.domains ?? [];
    if (domains.length === 0) return false; // 无领域标签(加载失败)
    if (!lockedDomain) return true; // 未锁定专用领域,均可选
    const specifics = specificDomainsOf(domains);
    return specifics.length === 0 || specifics.includes(lockedDomain); // 通用技能或同领域
  };

  const toggleSkill = (name: string) => {
    const skill = allSkills.find((s) => s.name === name);
    if (!skill) return;
    setSkills((prev) => {
      if (prev.includes(name)) return prev.filter((s) => s !== name);
      if (!isSkillSelectable(skill)) return prev; // 跨域,阻止勾选
      return [...prev, name];
    });
  };

  const selectAllSkills = () => {
    if (lockedDomain) {
      // 已锁定:只选同领域 + 通用技能
      setSkills(allSkills.filter(isSkillSelectable).map((s) => s.name));
    } else {
      // 未锁定:全选仅选通用技能,避免触发跨域冲突
      setSkills(
        allSkills.filter((s) => (s.domains ?? []).includes('general')).map((s) => s.name),
      );
    }
  };
  const deselectAllSkills = () => setSkills([]);

  const toggleCategory = (cat: string) => {
    setAutoCategories((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat],
    );
  };

  const handleRun = async () => {
    setRunning(true);
    setError('');
    try {
      await api.arenaRun({
        skills: skills.length > 0 ? skills.map((s) => `skills/${s}.md`) : undefined,
        task_source: taskSource,
        rounds_per_pair: rounds,
        max_improve_iterations: maxIterations,
        run_fusion: runFusion,
        run_improvement: runImprovement,
        auto_categories: taskSource !== 'fixed' ? effectiveAutoCategories : undefined,
        auto_per_category: taskSource !== 'fixed' ? autoPerCategory : 3,
      });
      setTimeout(() => api.arenaRuns().then(setRuns).catch(() => {}), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : '启动失败');
    } finally {
      setTimeout(() => setRunning(false), 2000);
    }
  };

  const handleReset = async () => {
    try {
      await api.arenaReset();
    } catch (e) {
      setError(e instanceof Error ? e.message : '重置失败');
    }
  };

  const matchIndex = status.match_index ?? 0;
  const totalMatches = status.total_matches ?? 0;
  const eloSnapshot = status.elo_snapshot ?? {};
  const currentPhase = status.phase ?? null;
  const currentDomain = status.domain ?? null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">
            竞技控制台
          </h1>
          {isReplaying && (
            <span className="text-xs text-amber-600 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded animate-pulse">
              ◉ 回放模式
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700 border border-gray-200 hover:border-gray-300 px-3 py-1.5 rounded-lg"
          >
            <History className="w-3.5 h-3.5" /> 历史 ({runs.length})
          </button>
          <div className="flex items-center gap-1.5 text-xs">
            <Radio className={`w-3.5 h-3.5 ${connected ? 'text-green-500' : 'text-gray-400'}`} />
            <span className={connected ? 'text-green-600' : 'text-gray-400'}>
              {connected ? '● LIVE' : '○ OFFLINE'}
            </span>
          </div>
        </div>
      </div>

      {/* History Panel */}
      {showHistory && (
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-gray-900 font-semibold text-sm">
              运行历史
            </h2>
            {isReplaying && (
              <button
                onClick={clearReplay}
                className="flex items-center gap-1 text-xs text-red-600 hover:text-red-800 border border-red-300 px-2 py-1 rounded"
              >
                <X className="w-3 h-3" /> 退出回放
              </button>
            )}
          </div>
          {runs.length === 0 ? (
            <div className="text-gray-400 text-sm py-3 text-center">
              暂无运行记录
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
              {runs.map((r) => (
                <button
                  key={r.filename}
                  onClick={() => loadReplay(r.filename)}
                  disabled={isReplaying && replayRunId === r.filename}
                  className={`text-left px-3 py-2 rounded-lg border text-xs transition-colors ${
                    replayRunId === r.filename
                      ? 'border-amber-300 bg-amber-50 text-amber-700'
                      : 'bg-gray-50 border-gray-200 hover:border-green-300 hover:bg-green-50 text-gray-600'
                  }`}
                >
                  <div className="font-bold text-gray-900">{r.run_id}</div>
                  <div className="text-gray-400 mt-0.5">
                    {(r.size / 1024).toFixed(1)} KB · {new Date(r.modified * 1000).toLocaleString()}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Phase Timeline */}
      <PhaseTimeline status={status} phase={currentPhase} domain={currentDomain} />

      {/* Battle Arena + Elo Leaderboard */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <BattleArena
          liveBattle={liveBattle}
          latestResult={latestResult}
          matchIndex={matchIndex}
          totalMatches={totalMatches}
        />
        <EloLeaderboard eloSnapshot={eloSnapshot} />
      </div>

      {/* Streaming Skill Output */}
      {skillOutputs.size > 0 && (
        <SkillOutputViewer outputs={skillOutputs} />
      )}

      {/* Controls + Event Log */}
      <div className="grid grid-cols-1 lg:grid-cols-[360px_1fr] gap-6">
        {/* Controls */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 space-y-3">
          <h2 className="text-gray-900 font-semibold text-sm">控制面板</h2>

          {/* Skills */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs text-gray-500">
                参与技能 (留空=全部，已选 {skills.length}/{allSkills.length})
              </label>
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={selectAllSkills}
                  disabled={isReplaying}
                  className="text-[10px] text-blue-600 hover:text-blue-800 disabled:opacity-30"
                >
                  {lockedDomain ? `全选(${lockedDomain})` : '全选通用'}
                </button>
                <span className="text-[10px] text-gray-300">|</span>
                <button
                  type="button"
                  onClick={deselectAllSkills}
                  disabled={isReplaying}
                  className="text-[10px] text-gray-500 hover:text-gray-800 disabled:opacity-30"
                >
                  清除
                </button>
              </div>
            </div>
            <div className="max-h-40 overflow-auto border border-gray-200 rounded-lg p-2 space-y-1.5">
              {allSkills.length === 0 && (
                <div className="text-xs text-gray-400">加载中...</div>
              )}
              {allSkills.map((s) => {
                const domains = s.domains ?? [];
                const noDomain = domains.length === 0; // 加载失败/无标签
                const isGeneric = domains.includes('general');
                const specifics = specificDomainsOf(domains);
                const domainLabels = isGeneric ? ['通用'] : specifics;
                const selectable = !noDomain && isSkillSelectable(s);
                const blocked = isReplaying || !selectable;
                const lockedOut =
                  !noDomain && !selectable && lockedDomain !== null;
                return (
                  <label
                    key={s.name}
                    title={
                      noDomain
                        ? '该技能无领域标签,无法参与竞技'
                        : lockedOut
                          ? `已锁定领域 ${lockedDomain},该技能(${specifics.join('/')})不可选`
                          : undefined
                    }
                    className={`flex items-center gap-2 text-xs px-1.5 py-1 rounded ${
                      blocked ? 'cursor-not-allowed opacity-40' : 'cursor-pointer hover:bg-gray-50'
                    } ${skills.includes(s.name) ? 'bg-blue-50/50' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={skills.includes(s.name)}
                      onChange={() => toggleSkill(s.name)}
                      disabled={blocked}
                      className="rounded border-gray-300 text-blue-600 focus:ring-blue-500 flex-shrink-0"
                    />
                    <span className="text-gray-700 truncate flex-1">{s.name}</span>
                    <span className="flex gap-1 flex-shrink-0">
                      {domainLabels.map((d: string) => (
                        <span
                          key={d}
                          className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${
                            isGeneric
                              ? 'bg-purple-100 text-purple-700'
                              : d === 'writing'
                                ? 'bg-blue-100 text-blue-700'
                                : d === 'coding'
                                  ? 'bg-green-100 text-green-700'
                                  : d === 'analysis'
                                    ? 'bg-amber-100 text-amber-700'
                                    : 'bg-gray-100 text-gray-600'
                          }`}
                        >
                          {d}
                        </span>
                      ))}
                    </span>
                  </label>
                );
              })}
            </div>
            {(lockedDomain || hasDomainConflict) && (
              <div
                className={`text-[10px] px-2 py-1 rounded ${
                  hasDomainConflict
                    ? 'text-red-600 bg-red-50 border border-red-200'
                    : 'text-amber-600 bg-amber-50 border border-amber-200'
                }`}
              >
                {hasDomainConflict
                  ? '⚠ 已选择多个领域,无法同时竞技(通用技能除外)。请取消其他领域的选择。'
                  : `已锁定领域:${lockedDomain} — 仅可选择该领域与通用技能`}
              </div>
            )}
          </div>

          {/* Task Source */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">任务来源</label>
            <select
              value={taskSource}
              onChange={(e) => setTaskSource(e.target.value)}
              disabled={isReplaying}
              className="w-full px-3 py-2 rounded-lg border border-gray-200 bg-white text-gray-700 text-sm focus:border-blue-400 outline-none"
            >
              <option value="fixed">FIXED</option>
              <option value="auto">AUTO</option>
              <option value="hybrid">HYBRID</option>
            </select>
          </div>

          {/* Rounds */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">每对轮数</label>
            <input
              type="number"
              value={rounds}
              onChange={(e) => setRounds(Number(e.target.value))}
              disabled={isReplaying}
              min={1}
              max={10}
              className="w-full px-3 py-2 rounded-lg border border-gray-200 bg-white text-gray-700 text-sm focus:border-blue-400 outline-none"
            />
          </div>

          {/* Max Iterations */}
          <div>
            <label className="text-xs text-gray-500 block mb-1">改进迭代</label>
            <input
              type="number"
              value={maxIterations}
              onChange={(e) => setMaxIterations(Number(e.target.value))}
              disabled={isReplaying}
              min={1}
              max={10}
              className="w-full px-3 py-2 rounded-lg border border-gray-200 bg-white text-gray-700 text-sm focus:border-blue-400 outline-none"
            />
          </div>

          {/* Auto categories (only when auto/hybrid) */}
          {taskSource !== 'fixed' && (
            <>
              <div>
                <label className="text-xs text-gray-500 block mb-1">自动类目</label>
                {lockedDomain && (
                  <div className="text-[10px] text-amber-600 mb-1">
                    类目已由所选技能锁定:{lockedDomain}
                  </div>
                )}
                <div className="flex flex-wrap gap-1.5">
                  {['writing', 'coding', 'analysis'].map((cat) => {
                    const catLocked = lockedDomain !== null;
                    return (
                      <label
                        key={cat}
                        className={`flex items-center gap-1 text-xs ${catLocked ? 'cursor-not-allowed' : 'cursor-pointer'}`}
                      >
                        <input
                          type="checkbox"
                          checked={effectiveAutoCategories.includes(cat)}
                          onChange={() => toggleCategory(cat)}
                          disabled={isReplaying || catLocked}
                          className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                        />
                        <span className={catLocked && cat !== lockedDomain ? 'text-gray-400' : 'text-gray-600'}>
                          {cat}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">每类目数</label>
                <input
                  type="number"
                  value={autoPerCategory}
                  onChange={(e) => setAutoPerCategory(Number(e.target.value))}
                  disabled={isReplaying}
                  min={1}
                  max={20}
                  className="w-full px-3 py-2 rounded-lg border border-gray-200 bg-white text-gray-700 text-sm focus:border-blue-400 outline-none"
                />
              </div>
            </>
          )}

          {/* Phase toggles */}
          <div className="space-y-2">
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={runFusion}
                onChange={(e) => setRunFusion(e.target.checked)}
                disabled={isReplaying}
                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span className="text-gray-600">阶段 B 融合</span>
            </label>
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={runImprovement}
                onChange={(e) => setRunImprovement(e.target.checked)}
                disabled={isReplaying}
                className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span className="text-gray-600">阶段 C 改进</span>
            </label>
          </div>

          {/* Action buttons */}
          <div className="space-y-2 pt-2">
            <button
              onClick={handleRun}
              disabled={running || status.running || isReplaying || hasDomainConflict}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-semibold hover:bg-blue-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              ▶ 运行 {`A${runFusion ? '→B' : ''}${runImprovement ? '→C' : ''}→D`}
            </button>
            <button
              onClick={handleReset}
              disabled={isReplaying}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 border border-red-300 text-red-600 rounded-lg text-sm hover:bg-red-50 disabled:opacity-30 transition-colors"
            >
              <RotateCcw className="w-3.5 h-3.5" /> 重置状态
            </button>
          </div>
          {error && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 px-3 py-2 rounded-lg">
              {error}
            </div>
          )}
        </div>

        {/* Event Log */}
        <EventLog events={events} />
      </div>
    </div>
  );
}
