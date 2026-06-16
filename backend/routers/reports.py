from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pathlib import Path

from arena.config import REPORTS_DIR
from ..deps import list_reports

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
def get_reports():
    return list_reports()


@router.get("/{filename}")
def get_report(filename: str):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    p = REPORTS_DIR / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return {"filename": filename, "content": p.read_text(encoding="utf-8")}
