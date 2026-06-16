from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from arena.config import REPORTS_DIR
from arena.orchestrator import ArenaOrchestrator, ORCHESTRATOR_STATE_FILE
from arena.skill_metadata import TASK_DOMAINS, parse_skill_domains

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/arena", tags=["arena"])

# 实时事件订阅者(SSE 连接列表)
_event_subscribers: list[asyncio.Queue] = []
_event_lock = threading.Lock()
_thread_local_loop: Any = None
# 当前运行状态(供 /status 端点查询)
_active_status: dict[str, Any] = {
    "running": False,
    "phase": None,        # "A" | "B" | "C" | "D" | None
    "domain": None,       # 当前领域
    "match_index": 0,
    "total_matches": 0,
    "latest_result": None,  # 最近一场 match 结果
    "elo_snapshot": {},     # 最新 Elo 快照
    "current_run_file": None,  # 当前 events.jsonl 文件名
    "current_battle": None,    # 当前正在跑的比赛(技能对)
}
# 当前运行的 background task 引用
_active_task: asyncio.Task | None = None
# 当前 run 事件落盘文件句柄
_current_event_file: Any = None
# 当前 run_id(由时间戳生成)
_current_run_id: str = ""

EVENTS_DIR = REPORTS_DIR / "events"
EVENTS_DIR.mkdir(parents=True, exist_ok=True)


class RunRequest(BaseModel):
    skills: list[str] | None = None
    task_source: str = "fixed"
    rounds_per_pair: int = 2
    max_improve_iterations: int = 2
    run_fusion: bool = True
    run_improvement: bool = True
    auto_categories: list[str] | None = None
    auto_per_category: int = 3


def _all_skill_paths() -> list[str]:
    from arena.config import SKILLS_DIR
    if not SKILLS_DIR.exists():
        return []
    return [str(p) for p in sorted(SKILLS_DIR.glob("*.md"))]


def _validate_skill_domains(skill_paths: list[str]) -> None:
    """跨域防护:专用领域(writing/coding/analysis)之间互斥,通用(general)除外。

    若选中技能横跨多个专用领域,抛 400 拒绝;无领域标签的技能也拒绝。
    """
    specific_domains: set[str] = set()
    for p in skill_paths:
        path = Path(p)
        try:
            domains = parse_skill_domains(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"技能 {path.name} 无有效领域标签: {exc}",
            )
        specific_domains |= {d for d in domains if d in TASK_DOMAINS}
    if len(specific_domains) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"不能同时选择不同领域的技能(通用除外): "
                f"{sorted(specific_domains)}。请只保留一个领域。"
            ),
        )


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append_event_to_file(event: dict[str, Any]) -> None:
    """把事件追加到当前 run 的 jsonl 文件。"""
    global _current_event_file
    if _current_event_file is None:
        return
    try:
        _current_event_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        _current_event_file.flush()
    except Exception as exc:
        logger.warning("写事件文件失败: %s", exc)


def _set_loop() -> None:
    global _thread_local_loop
    _thread_local_loop = asyncio.get_event_loop()


def _emit_event(event: dict[str, Any]) -> None:
    """线程安全的事件投递。在 executor 线程内调用,通过 call_soon_threadsafe
    把 SSE 消息投回 event loop 的队列。
    """
    if "ts" not in event:
        event = {**event, "ts": _now_iso()}

    _append_event_to_file(event)

    payload = json.dumps(event, ensure_ascii=False)
    msg = f"data: {payload}\n\n"

    # 线程安全：锁保护 subscriber 读，通过 call_soon_threadsafe 投递到 event loop
    with _event_lock:
        queued = list(_event_subscribers)

    loop = asyncio.get_event_loop() if _thread_local_loop is None else _thread_local_loop

    def _put_loop(q: asyncio.Queue) -> None:
        try:
            loop.call_soon_threadsafe(q.put_nowait, msg)
        except asyncio.QueueFull:
            pass

    for q in queued:
        try:
            _put_loop(q)
        except Exception:
            pass


def _update_status_from_event(event: dict[str, Any]) -> None:
    """根据事件更新 _active_status,供 /status 端点查询。"""
    et = event.get("type", "")

    if et == "cycle_start":
        _active_status["running"] = True
        _active_status["match_index"] = 0
        _active_status["latest_result"] = None
        _active_status["elo_snapshot"] = {}

    elif et == "phase_a_plan":
        _active_status["total_matches"] = event.get("total_matches", 0)

    elif et == "phase_a_domain_start":
        _active_status["domain"] = event.get("domain")
        _active_status["phase"] = "A"

    elif et == "phase_a_match":
        _active_status["match_index"] = event.get("match_index", _active_status["match_index"])
        _active_status["latest_result"] = {
            "domain": event.get("domain"),
            "skill_a": event.get("skill_a"),
            "skill_b": event.get("skill_b"),
            "winner": event.get("winner"),
            "score_a": event.get("score_a"),
            "score_b": event.get("score_b"),
            "elo_a": event.get("elo_a"),
            "elo_b": event.get("elo_b"),
        }
        _active_status["current_battle"] = {
            "skill_a": event.get("skill_a"),
            "skill_b": event.get("skill_b"),
            "domain": event.get("domain"),
            "match_id": event.get("match_id"),
        }

    elif et == "phase_a_domain_done":
        snap = event.get("elo_snapshot", {})
        if isinstance(snap, dict):
            _active_status["elo_snapshot"].update(snap)

    elif et == "phase_b_fuse_start":
        _active_status["phase"] = "B"
        _active_status["domain"] = event.get("domain")

    elif et == "phase_c_improve_start":
        _active_status["phase"] = "C"
        _active_status["domain"] = event.get("domain")

    elif et == "phase_done":
        # 阶段完成后清除领域信息
        _active_status["domain"] = None
        _active_status["current_battle"] = None

    elif et == "run_start":
        _active_status["running"] = True
        _active_status["phase"] = None

    elif et == "run_end":
        _active_status["running"] = False
        _active_status["phase"] = None

    elif et == "phase_start":
        phase = event.get("phase")
        if phase in ("A", "B", "C", "D"):
            _active_status["phase"] = phase
            if phase != "A":
                _active_status["domain"] = None
                _active_status["current_battle"] = None

    elif et == "cycle_complete" or et == "cycle_error":
        _active_status["running"] = False
        if et == "cycle_error":
            _active_status["error"] = event.get("error")


def _reconstruct_status_from_disk() -> None:
    """后端启动时从持久化数据恢复 _active_status(进程重启后状态保留)。

    最近一次运行的事件文件( reports/events/run_*.jsonl )回放重建
    phase / match 计数 / latest_result / current_battle / elo 快照;
    running 强制置 false(重启时 executor 线程已不存在,进行中的 run 视为中断)。
    elo 再用 elo_state.json 覆盖,确保是权威值。
    """
    _active_status.update({
        "running": False,
        "phase": None,
        "domain": None,
        "match_index": 0,
        "total_matches": 0,
        "latest_result": None,
        "current_battle": None,
        "elo_snapshot": {},
        "current_run_file": None,
    })
    runs = sorted(
        (p for p in EVENTS_DIR.glob("run_*.jsonl") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if runs:
        latest = runs[0]
        _active_status["current_run_file"] = latest.name
        try:
            for line in latest.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _update_status_from_event(evt)
        except OSError as exc:  # noqa: BLE001
            logger.warning("回放最近运行事件失败: %s", exc)

    # elo 用持久化的 elo_state.json 覆盖(权威源,跨多场 run 也准确)
    try:
        from arena.elo import load_domain_state
        for ratings in load_domain_state().values():
            _active_status["elo_snapshot"].update(ratings)
    except Exception:  # noqa: BLE001
        pass

    # 重启后无活跃 executor → 强制非运行态
    _active_status["running"] = False


# 模块加载时恢复状态:进程重启后 /status 仍能返回上次运行快照
_reconstruct_status_from_disk()


@router.get("/status")
def arena_status():
    """返回当前运行状态。"""
    if ORCHESTRATOR_STATE_FILE.exists():
        try:
            state = json.loads(ORCHESTRATOR_STATE_FILE.read_text(encoding="utf-8"))
            phases = state.get("phases", {})
            phase_summary = {
                k: v.get("status", "unknown")
                for k, v in phases.items()
            }
        except (json.JSONDecodeError, OSError):
            phase_summary = {}
    else:
        phase_summary = {}

    return {
        "running": _active_status["running"],
        "phase": _active_status.get("phase"),
        "domain": _active_status.get("domain"),
        "match_index": _active_status.get("match_index", 0),
        "total_matches": _active_status.get("total_matches", 0),
        "latest_result": _active_status.get("latest_result"),
        "current_battle": _active_status.get("current_battle"),
        "elo_snapshot": _active_status.get("elo_snapshot", {}),
        "current_run_file": _active_status.get("current_run_file"),
        "phases": phase_summary,
    }


@router.get("/events")
async def arena_events():
    """SSE 实时事件流。"""
    _set_loop()  # 记录 event loop 用于线程安全投递

    async def event_generator():
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with _event_lock:
            _event_subscribers.append(q)
        try:
            # 立即推一帧 status,让前端不用等
            yield f"data: {json.dumps({'type': 'sse_open', 'ts': _now_iso()}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            with _event_lock:
                if q in _event_subscribers:
                    _event_subscribers.remove(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _on_progress(event: dict[str, Any]) -> None:
    """注入到 ArenaOrchestrator 的 progress_callback。

    此函数在 executor 线程中调用,绝不能直接 await。
    """
    _update_status_from_event(event)
    _emit_event(event)


@router.post("/run")
async def arena_run(req: RunRequest):
    global _active_task, _current_event_file, _current_run_id

    if _active_status["running"]:
        raise HTTPException(status_code=409, detail="已有竞技任务在运行中")

    skill_paths = req.skills or _all_skill_paths()
    if not skill_paths:
        raise HTTPException(status_code=400, detail="没有可用 skills")

    # 跨域防护:专用领域之间互斥(通用除外)
    _validate_skill_domains(skill_paths)

    # 开启新 run,创建事件文件
    _current_run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    event_file_path = EVENTS_DIR / f"run_{_current_run_id}.jsonl"
    _current_event_file = open(event_file_path, "w", encoding="utf-8")
    _active_status["current_run_file"] = event_file_path.name

    _emit_event({
        "type": "run_start",
        "run_id": _current_run_id,
        "skills": skill_paths,
        "task_source": req.task_source,
        "rounds_per_pair": req.rounds_per_pair,
    })

    async def _run() -> None:
        global _active_task, _current_event_file
        try:
            # orchestrator 在 executor 线程中跑,会调用 _on_progress
            orch = ArenaOrchestrator(progress_callback=_on_progress)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: orch.run_full_cycle(
                    skill_paths=skill_paths,
                    task_source=req.task_source,
                    auto_categories=req.auto_categories,
                    auto_per_category=req.auto_per_category,
                    rounds_per_pair=req.rounds_per_pair,
                    max_improve_iterations=req.max_improve_iterations,
                    run_fusion=req.run_fusion,
                    run_improvement=req.run_improvement,
                ),
            )
        except Exception as exc:
            logger.exception("arena run 失败")
            _emit_event({"type": "cycle_error", "error": str(exc), "phase": _active_status.get("phase")})
        finally:
            _emit_event({"type": "run_end", "run_id": _current_run_id})
            if _current_event_file is not None:
                _current_event_file.close()
                _current_event_file = None
            _active_task = None

    _active_task = asyncio.create_task(_run())
    return {"status": "started", "run_id": _current_run_id, "event_file": event_file_path.name}


@router.delete("/state")
def arena_reset():
    if ORCHESTRATOR_STATE_FILE.exists():
        ORCHESTRATOR_STATE_FILE.unlink()
    return {"status": "reset", "message": "orchestrator state 已清空"}


# ============================================================
# 历史运行回放端点
# ============================================================


@router.get("/runs")
def list_runs():
    """列出所有历史运行(基于 reports/events/run_*.jsonl)。"""
    if not EVENTS_DIR.exists():
        return []
    runs = []
    for p in sorted(EVENTS_DIR.glob("run_*.jsonl"), reverse=True):
        stat = p.stat()
        runs.append({
            "filename": p.name,
            "run_id": p.stem.replace("run_", ""),
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })
    return runs


@router.get("/runs/{filename}")
def get_run_events(filename: str):
    """返回某次运行的完整事件流(JSONL → JSON 数组)。"""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    p = EVENTS_DIR / filename
    if not p.exists() or not filename.startswith("run_"):
        raise HTTPException(status_code=404, detail="运行记录不存在")
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"filename": filename, "event_count": len(events), "events": events}
