from __future__ import annotations

from fastapi import APIRouter

from ..deps import get_elo_data

router = APIRouter(prefix="/api/elo", tags=["elo"])


@router.get("")
def get_elo():
    return get_elo_data()


@router.get("/{domain}")
def get_elo_domain(domain: str):
    elo = get_elo_data()
    if not elo:
        return {}
    if domain in elo and isinstance(elo[domain], dict):
        ratings = elo[domain]
    else:
        ratings = elo
    sorted_ratings = sorted(
        ((n, r) for n, r in ratings.items() if not n.startswith("baseline")),
        key=lambda x: x[1],
        reverse=True,
    )
    return {"domain": domain, "leaderboard": sorted_ratings}
