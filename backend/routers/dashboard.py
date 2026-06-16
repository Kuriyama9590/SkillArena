from __future__ import annotations

from fastapi import APIRouter

from ..deps import compute_dashboard_stats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard():
    return compute_dashboard_stats()
