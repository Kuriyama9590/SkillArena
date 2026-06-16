from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Generator

from fastapi import HTTPException

from arena.config import (
    ELO_STATE_FILE,
    REPORTS_DIR,
    SKILLS_DIR,
    TASKS_DIR,
    TASKS_AUTO_DIR,
)
from arena.orchestrator import (
    ORCHESTRATOR_STATE_FILE,
    MATCHES_LOG,
    ArenaOrchestrator,
)
from arena.report import MatchResult

logger = logging.getLogger(__name__)


def get_elo_data() -> dict[str, Any]:
    """Load current Elo state (domain-structured preferred, flat fallback)."""
    if not ELO_STATE_FILE.exists():
        return {}
    raw = json.loads(ELO_STATE_FILE.read_text(encoding="utf-8"))
    return raw


def get_orchestrator_state() -> dict[str, Any]:
    """Load orchestrator state.json."""
    if not ORCHESTRATOR_STATE_FILE.exists():
        return {}
    try:
        return json.loads(ORCHESTRATOR_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def read_matches(limit: int = 500, offset: int = 0, domain: str | None = None) -> list[dict]:
    """Read matches from JSONL file."""
    if not MATCHES_LOG.exists():
        return []
    lines = MATCHES_LOG.read_text(encoding="utf-8").strip().splitlines()
    records = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if domain and rec.get("domain") != domain:
                continue
            records.append(rec)
        except json.JSONDecodeError:
            continue
    return records[offset : offset + limit]


def list_skill_files() -> list[dict[str, Any]]:
    """List all skill .md files with metadata."""
    if not SKILLS_DIR.exists():
        return []
    from arena.skill_metadata import parse_skill_domains

    result = []
    for p in sorted(SKILLS_DIR.glob("*.md")):
        try:
            content = p.read_text(encoding="utf-8")
            domains = parse_skill_domains(p)
            result.append(
                {
                    "name": p.stem,
                    "filename": p.name,
                    "path": str(p),
                    "domains": domains,
                    "content_length": len(content),
                    "preview": content[:200],
                }
            )
        except Exception:
            result.append(
                {
                    "name": p.stem,
                    "filename": p.name,
                    "path": str(p),
                    "domains": [],
                    "content_length": 0,
                    "preview": "",
                    "error": "未声明 domains 且无法自动推断",
                }
            )
    return result


def read_skill_content(name: str) -> str | None:
    """Read a single skill file content by stem name."""
    p = SKILLS_DIR / f"{name}.md"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def list_task_files() -> list[dict[str, Any]]:
    """List all fixed tasks grouped by file."""
    if not TASKS_DIR.exists():
        return []
    import yaml

    result = []
    for p in sorted(TASKS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            tasks = data if isinstance(data, list) else data.get("tasks", [])
            result.append(
                {
                    "filename": p.name,
                    "category": p.stem,
                    "task_count": len(tasks),
                    "tasks": tasks,
                }
            )
        except Exception as exc:
            logger.warning("list_task_files: %s: %s", p, exc)
    return result


def list_reports() -> list[dict[str, Any]]:
    """List generated report files."""
    if not REPORTS_DIR.exists():
        return []
    result = []
    for p in sorted(REPORTS_DIR.glob("report_*.md"), reverse=True):
        stat = p.stat()
        result.append(
            {
                "filename": p.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return result


def compute_dashboard_stats() -> dict[str, Any]:
    """Compute aggregate stats for the dashboard."""
    skills = list_skill_files()
    elo_raw = get_elo_data()
    matches = read_matches(limit=10000)

    domain_count: dict[str, int] = {}
    for s in skills:
        for d in s["domains"]:
            domain_count[d] = domain_count.get(d, 0) + 1

    domain_top: dict[str, dict[str, Any]] = {}
    if isinstance(elo_raw, dict):
        for domain_key, ratings in elo_raw.items():
            if isinstance(ratings, dict):
                non_baseline = {
                    n: r for n, r in ratings.items() if not n.startswith("baseline")
                }
                if non_baseline:
                    top_name = max(non_baseline, key=lambda n: non_baseline[n])
                    domain_top[domain_key] = {
                        "name": top_name,
                        "elo": non_baseline[top_name],
                    }
    elif elo_raw:
        non_baseline = {
            n: r for n, r in elo_raw.items() if not n.startswith("baseline")
        }
        if non_baseline:
            top_name = max(non_baseline, key=lambda n: non_baseline[n])
            domain_top["all"] = {"name": top_name, "elo": non_baseline[top_name]}

    recent_matches = matches[-5:] if matches else []

    return {
        "total_skills": len(skills),
        "total_matches": len(matches),
        "domains": domain_count,
        "domain_top": domain_top,
        "recent_matches": recent_matches,
    }
