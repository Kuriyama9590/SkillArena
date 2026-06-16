from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


@dataclass
class ArenaJob:
    job_id: str
    status: str = "pending"
    phase: str = ""
    progress: float = 0.0
    message: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    result: dict[str, Any] | None = None
    error: str = ""
    log_lines: list[str] = field(default_factory=list)


@dataclass
class ArenaRunner:
    _current_job: ArenaJob | None = None
    _subscribers: list[asyncio.Queue[str]] = field(default_factory=list)

    def get_job(self) -> ArenaJob | None:
        return self._current_job

    async def subscribe(self) -> AsyncGenerator[str, None]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.append(q)
        try:
            while True:
                data = await q.get()
                yield data
                if data.startswith("event: done") or data.startswith("event: error"):
                    break
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast(self, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps({"event": event, **data}, ensure_ascii=False)
        msg = f"event: {event}\ndata: {payload}\n\n"
        for q in self._subscribers:
            q.put_nowait(msg)

    def _log(self, line: str) -> None:
        if self._current_job:
            self._current_job.log_lines.append(line)
        logger.info("[arena-job] %s", line)

    async def run_full_cycle(
        self,
        skill_paths: list[str],
        task_source: str = "fixed",
        rounds_per_pair: int = 2,
        max_improve_iterations: int = 2,
        run_fusion: bool = True,
        run_improvement: bool = True,
    ) -> ArenaJob:
        if self._current_job and self._current_job.status == "running":
            raise RuntimeError("已有竞技任务在运行中")

        job = ArenaJob(
            job_id=f"job-{int(time.time())}",
            status="running",
            started_at=time.time(),
        )
        self._current_job = job

        async def _worker() -> None:
            try:
                import logging
                from arena.config import get_settings
                from arena.deepseek_client import DeepSeekClient
                from arena.orchestrator import ArenaOrchestrator

                self._log("初始化竞技场...")
                self._broadcast("status", {"status": "running", "phase": "init", "message": "初始化中..."})

                settings = get_settings()
                client = DeepSeekClient(settings=settings)
                orch = ArenaOrchestrator(client=client)

                self._log(f"开始运行: skills={len(skill_paths)}, task_source={task_source}")
                self._broadcast("status", {"status": "running", "phase": "running", "message": "竞技进行中..."})

                loop = asyncio.get_event_loop()
                report = await loop.run_in_executor(
                    None,
                    lambda: orch.run_full_cycle(
                        skill_paths=skill_paths,
                        task_source=task_source,
                        rounds_per_pair=rounds_per_pair,
                        max_improve_iterations=max_improve_iterations,
                        run_fusion=run_fusion,
                        run_improvement=run_improvement,
                    ),
                )

                job.status = "done"
                job.finished_at = time.time()
                job.progress = 1.0
                job.result = {
                    "matches": len(report.matches),
                    "fused_skill": str(report.fused_skill) if report.fused_skill else None,
                    "improvement_iterations": (
                        report.improvement.total_iterations if report.improvement else 0
                    ),
                    "report_path": str(report.report_path) if report.report_path else None,
                    "notes": report.notes,
                }
                self._log(f"完成: {len(report.matches)} 场比赛")
                self._broadcast("status", {"status": "done", "progress": 1.0, "result": job.result})

            except Exception as exc:
                job.status = "error"
                job.finished_at = time.time()
                job.error = str(exc)
                self._log(f"错误: {exc}")
                self._broadcast("status", {"status": "error", "error": str(exc)})
                logger.exception("arena job failed")

        asyncio.create_task(_worker())
        return job

    async def run_single_phase(
        self,
        phase: str,
        skill_paths: list[str],
        task_source: str = "fixed",
        rounds_per_pair: int = 2,
        max_improve_iterations: int = 2,
    ) -> ArenaJob:
        if self._current_job and self._current_job.status == "running":
            raise RuntimeError("已有竞技任务在运行中")

        job = ArenaJob(
            job_id=f"job-{phase}-{int(time.time())}",
            status="running",
            phase=phase,
            started_at=time.time(),
        )
        self._current_job = job

        async def _worker() -> None:
            try:
                from arena.config import get_settings
                from arena.deepseek_client import DeepSeekClient
                from arena.orchestrator import ArenaOrchestrator

                self._log(f"开始阶段 {phase}...")
                self._broadcast("status", {"status": "running", "phase": phase, "message": f"阶段 {phase} 进行中..."})

                settings = get_settings()
                client = DeepSeekClient(settings=settings)
                orch = ArenaOrchestrator(client=client)

                loop = asyncio.get_event_loop()

                if phase == "A":
                    from arena.skill_metadata import load_skill_entry
                    skills = {}
                    for p in skill_paths:
                        entry = load_skill_entry(Path(p))
                        skills[entry.name] = entry
                    from arena.orchestrator import _load_fixed_tasks
                    from arena.config import TASKS_DIR
                    tasks = []
                    for path in sorted(TASKS_DIR.glob("*.yaml")) if TASKS_DIR.exists() else []:
                        for t in _load_fixed_tasks(path):
                            tasks.append(dict(t))
                    state = orch._new_state(skill_paths=skill_paths, task_source=task_source)
                    matches = await loop.run_in_executor(
                        None,
                        lambda: orch._phase_arena(
                            state=state, skills=skills, tasks=tasks,
                            rounds_per_pair=rounds_per_pair,
                        ),
                    )
                    job.result = {"matches": len(matches), "phase": "A"}

                elif phase == "B":
                    from arena.elo import load_domain_state
                    from arena.skill_metadata import load_skill_entry
                    skills = {}
                    for p in skill_paths:
                        entry = load_skill_entry(Path(p))
                        skills[entry.name] = entry
                    domain_elo = load_domain_state()
                    state = orch._new_state(skill_paths=skill_paths, task_source=task_source)
                    fused_list = []
                    for domain in ("writing", "coding", "analysis"):
                        elo_dom = domain_elo.get(domain, {})
                        non_base = {n: r for n, r in elo_dom.items() if not n.startswith("baseline")}
                        if len(non_base) >= 2:
                            top2 = orch._top_k_skills(elo_dom, k=2)
                            if len(top2) == 2:
                                fp, fc = await loop.run_in_executor(
                                    None,
                                    lambda d=domain, t=top2: orch._phase_fusion(
                                        state=state, skill_a_path=t[0], skill_b_path=t[1],
                                        skills=skills, output_name=None, domain=d,
                                    ),
                                )
                                fused_list.append(str(fp))
                    job.result = {"fused_skills": fused_list, "phase": "B"}

                elif phase == "C":
                    from arena.elo import load_domain_state
                    from arena.skill_metadata import load_skill_entry
                    skills = {}
                    for p in skill_paths:
                        entry = load_skill_entry(Path(p))
                        skills[entry.name] = entry
                    domain_elo = load_domain_state()
                    state = orch._new_state(skill_paths=skill_paths, task_source=task_source)
                    imp_list = []
                    for domain in ("writing", "coding", "analysis"):
                        elo_dom = domain_elo.get(domain, {})
                        non_base = {n: r for n, r in elo_dom.items() if not n.startswith("baseline")}
                        if non_base:
                            bottom = orch._bottom_skill(elo_dom)
                            if bottom and bottom in skills:
                                imp = await loop.run_in_executor(
                                    None,
                                    lambda d=domain, b=bottom: orch._phase_improvement(
                                        state=state, skill_name=b,
                                        skill_content=skills[b].content,
                                        max_iterations=max_improve_iterations, domain=d,
                                    ),
                                )
                                imp_list.append({"skill": bottom, "iterations": imp.total_iterations})
                    job.result = {"improvements": imp_list, "phase": "C"}

                elif phase == "D":
                    path = await loop.run_in_executor(None, lambda: orch.regenerate_report())
                    job.result = {"report_path": str(path), "phase": "D"}

                job.status = "done"
                job.finished_at = time.time()
                job.progress = 1.0
                self._log(f"阶段 {phase} 完成")
                self._broadcast("status", {"status": "done", "phase": phase, "result": job.result})

            except Exception as exc:
                job.status = "error"
                job.finished_at = time.time()
                job.error = str(exc)
                self._log(f"阶段 {phase} 错误: {exc}")
                self._broadcast("status", {"status": "error", "phase": phase, "error": str(exc)})
                logger.exception("arena phase job failed")

        asyncio.create_task(_worker())
        return job


_runner: ArenaRunner | None = None


def get_runner() -> ArenaRunner:
    global _runner
    if _runner is None:
        _runner = ArenaRunner()
    return _runner
