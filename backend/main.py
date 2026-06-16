from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import arena, dashboard, elo, matches, reports, skills, tasks

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Skill Arena API starting")
    yield
    logger.info("Skill Arena API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Skill Arena API",
        version="0.3.0",
        description="Skill竞技场后端API",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(skills.router)
    app.include_router(elo.router)
    app.include_router(matches.router)
    app.include_router(arena.router)
    app.include_router(tasks.router)
    app.include_router(reports.router)
    app.include_router(dashboard.router)

    if FRONTEND_DIST.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

    return app


app = create_app()
