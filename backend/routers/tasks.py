from __future__ import annotations

from fastapi import APIRouter

from ..deps import list_task_files

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def get_tasks():
    return list_task_files()
