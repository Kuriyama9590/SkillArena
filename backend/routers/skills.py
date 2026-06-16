from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import list_skill_files, read_skill_content

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _safe_name(name: str) -> str:
    """Sanitize skill name to a safe filename."""
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip())


@router.get("")
def get_skills():
    return list_skill_files()


@router.get("/{name}")
def get_skill(name: str):
    content = read_skill_content(name)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return {"name": name, "content": content}


@router.post("")
@router.post("/upload")
def upload_skill(data: dict):
    from arena.config import SKILLS_DIR

    name = data.get("name", "").strip()
    content = data.get("content", "").strip()
    if not name or not content:
        raise HTTPException(status_code=400, detail="name and content required")
    safe = _safe_name(name)
    path = SKILLS_DIR / f"{safe}.md"
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{safe}' already exists")
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"name": safe, "path": str(path), "created": True}


@router.put("/{name}")
def update_skill(name: str, data: dict):
    from arena.config import SKILLS_DIR

    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    safe = _safe_name(name)
    path = SKILLS_DIR / f"{safe}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{safe}' not found")
    path.write_text(content, encoding="utf-8")
    return {"name": safe, "path": str(path), "updated": True}


@router.delete("/{name}")
def delete_skill(name: str):
    from arena.config import SKILLS_DIR

    safe = _safe_name(name)
    path = SKILLS_DIR / f"{safe}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{safe}' not found")
    path.unlink()
    return {"name": safe, "deleted": True}
