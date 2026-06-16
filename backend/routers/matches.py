from __future__ import annotations

from fastapi import APIRouter, Query

from ..deps import read_matches

router = APIRouter(prefix="/api/matches", tags=["matches"])


@router.get("")
def get_matches(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    domain: str | None = Query(default=None),
):
    return {"matches": read_matches(limit=limit, offset=offset, domain=domain)}


@router.get("/stats")
def get_match_stats():
    all_matches = read_matches(limit=10000)
    by_domain: dict[str, int] = {}
    by_winner: dict[str, int] = {"A": 0, "B": 0, "tie": 0}
    for m in all_matches:
        d = m.get("domain", "unknown")
        by_domain[d] = by_domain.get(d, 0) + 1
        w = m.get("verdict", {}).get("winner", "tie")
        if w in by_winner:
            by_winner[w] += 1
    return {"total": len(all_matches), "by_domain": by_domain, "by_winner": by_winner}
