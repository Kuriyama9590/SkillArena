from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import list_skill_files, read_skill_content

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
def get_skills():
    return list_skill_files()


@router.get("/{name}")
def get_skill(name: str):
    content = read_skill_content(name)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return {"name": name, "content": content}


@router.post("/upload")
def upload_skill(data: dict):
    from pathlib import Path
    from arena.config import SKILLS_DIR

    name = data.get("name", "").strip()
    content = data.get("content", "").strip()
    if not name or not content:
        raise HTTPException(status_code=400, detail="name and content required")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    path = SKILLS_DIR / f"{safe}.md"
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"name": safe, "path": str(path), "created": True}
