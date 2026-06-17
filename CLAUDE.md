# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

SkillArena compares "prompt skills" (system-prompt engineering) on a fixed task set: multiple skills guide the same execution model to produce outputs, a judge model blind-evaluates them, and a per-track Elo ranking accumulates across runs.

**Authoritative requirements/spec:** `docs/REQUIREMENTS.md`. Read it before making changes that touch the competition model — it is the current baseline and supersedes the outdated CLI framing in `README.md`.

**Primary deliverable is the Web app** (FastAPI `backend/` + React `frontend/`). The CLI (`arena/__main__.py`) is deprecated and retained only for maintenance. Do not add CLI features.

`tasks/todo.md` tracks pending work; `deliverable-*.md` and `FIX_PLAN.md` are historical snapshots, not current truth.

## Commands

```bash
# Dev: start FastAPI backend (port 8000) + Vite frontend (port 5173) concurrently
python scripts/dev.py            # recommended; Windows: dev.cmd
# Frontend: http://localhost:5173 (HMR, proxies /api → :8000)
# API docs:  http://localhost:8000/docs

# Backend only
python -m uvicorn backend.main:app --port 8000

# Tests (all mock; no network except e2e)
python -m pytest                       # full suite
python -m pytest tests/test_orchestrator.py::TestRunFullCycleEndToEnd -v   # single test
python -m pytest tests/ -k elo -v      # by keyword

# Real-DeepSeek-API smoke (opt-in, costs tokens; ≤3 API calls)
RUN_E2E_SMOKE=1 pytest tests/test_e2e_smoke.py -v -s

# Frontend type-check + build
cd frontend && npm run build           # tsc -b && vite build
cd frontend && npx tsc --noEmit        # type-check only

# Backend import sanity
python -c "from backend.main import app; print('OK')"
```

Setup: `pip install -e ".[dev]"`, `cp .env.example .env` and set `DEEPSEEK_API_KEY`. Python ≥3.10.

## Architecture

### Three-layer structure

- **`arena/` — core engine (framework-agnostic, no HTTP).** The competition physics. Imported by both the deprecated CLI and the Web backend.
  - `orchestrator.py` — `ArenaOrchestrator.run_full_cycle()` is the spine: a 4-phase pipeline **A→B→C→D** (arena pairwise matches → fuse top-2 → self-improve bottom-1 → report). Emits SSE events via `self._emit()` at each step. Checkpoint-resumes from `reports/orchestrator_state.json`.
  - `skill_metadata.py` — skill→track assignment. `VALID_DOMAINS`/`TASK_DOMAINS` define the track vocabulary; `parse_skill_domains()` reads YAML front matter `domains:`, falls back to filename/content inference, else raises (no silent `general` fallback). `participates_in()` makes `general` match all tracks.
  - `judge.py` — blind evaluation. `JUDGE_DIMENSIONS = (correctness, completeness, clarity, creativity)`, each 0–10. `Verdict.to_score()` maps **only `winner`** (A/B/tie → 1.0/0.5/0.0) into the Elo input — dimension scores never enter Elo, they feed only the `reasoning` critique used later for improvement.
  - `elo.py` — K=32, initial 1500, **per-track storage** (`load_domain_state`/`save_domain_state` → `{track: {skill: rating}}` at `reports/elo_state.json`). `baseline` is a plain player (no special Elo logic).
  - `deepseek_client.py` — OpenAI-compatible client; `execute()` for full responses, `execute_stream()` for token streaming via `on_chunk` callback.
  - `fuse.py` / `self_improve.py` — artifact generation with retry-once + length/structure validation.
- **`backend/` — FastAPI HTTP layer over `arena/`.** Routers: `arena` (run/status/SSE/events/runs), `skills` (CRUD), `elo`, `matches`, `reports`, `tasks`, `dashboard`. The heavy one is `arena.py`:
  - `POST /run` runs `orch.run_full_cycle` in a thread executor; SSE `/events` streams events to subscribers. **Thread-safety matters here** — `_emit_event` runs on the executor thread and must use `_event_lock` + `call_soon_threadsafe` to feed the asyncio event loop. If you touch event emission, preserve this.
  - `_reconstruct_status_from_disk()` (called at module load) replays the most recent `reports/events/run_*.jsonl` to rebuild `_active_status` after a process restart (forces `running=False`), then overlays `elo_state.json`. This is what makes "refresh survives restart" work.
- **`frontend/` — React 19 + Vite + Tailwind v4 + react-router.** `src/api/index.ts` is the typed API client; `src/hooks/useArenaStatus.ts` consumes the SSE stream (EventSource → `/api/arena/events`) and reconstructs `liveBattle`/`latestResult`/`skillOutputs` from events; pages under `src/pages/`, components under `src/components/`. Vite proxies `/api` → `:8000`.

### Live event flow (the key cross-cutting path)

`orchestrator` emits → `_emit_event` appends to `reports/events/run_<ts>.jsonl` **and** broadcasts to SSE subscribers → frontend `useArenaStatus.handleEvent` updates UI. Event types include `phase_a_match` (score/winner), `skill_output_start/chunk/done` (streaming), `phase_b_fuse_*`, `phase_c_iteration`. The on-disk jsonl doubles as the replay source (`GET /runs/{filename}` → `loadReplay`).

### Track isolation (current code reality)

Tracks are `writing/coding/analysis` + `general` **today**; `REQUIREMENTS.md` defines a migration to 6 tracks (code/writing/reasoning/roleplay/instruction/longtext). Two enforcement points: `_validate_skill_domains` (`backend/routers/arena.py`) rejects cross-specialty skill selection at the API; `_domain_is_active` (`arena/orchestrator.py`) requires a specialty skill to anchor a track (general alone can't open one). `baseline` is a no-skill virtual player that joins every track's pairings but is filtered out of top-2/bottom-1 selection.

## Gotchas

- The CLI in `README.md`'s usage sections does not reflect the Web-first reality — trust `REQUIREMENTS.md`, not `README.md`, for behavior contracts.
- `gen-*` skill files still exist in `skills/` despite `tasks/todo.md` claiming they were deleted — verify before relying on that claim.
- Tests are fully mocked (154 passing, 1 e2e skipped). No real API calls happen unless `RUN_E2E_SMOKE=1`.
- `arena/` has no dependency on `backend/` or `frontend/` — keep it that way (the engine must stay HTTP-free).
- All file writes use `pathlib.Path`; outputs land under `reports/` (state, events, fused, improved, reports, matches.jsonl).

## Working style: delegate to subagents

**Be aggressive with subagents to keep the main context clean.** Launch research, exploration, and parallel fact-checking as subagents; keep only the conclusions in the main thread. Spawn independent work in a single message (multiple tool calls) so it runs concurrently.

- **Investigating "how does X work" / verifying code facts** → fan out parallel `Explore` subagents, each scoped to one area (e.g. one for `arena/judge.py`+`elo.py`, one for the backend SSE layer, one for the frontend hooks). Demand `file:line` evidence, not prose.
- **Auditing a doc or claim against code** → a `haiku` Explore agent is ideal: cheap, fast, good at literal cross-checking. The grill session that produced `REQUIREMENTS.md` was verified this way.
- **Model tiers**: `haiku` (the user calls it v4-flash) for mechanical fact-checking, grep-style sweeps, and narrow lookups; `sonnet` for broader exploration and design/research tasks that need judgment. Use both liberally — they're cheap relative to the main context they save.
- **Don't re-verify codegraph/subagent results with grep yourself** — that repeats work already done. Trust structured findings; only raw-Read a specific detail the subagent didn't cover.

Pattern that works well here: do a parallel fact-gathering fan-out (haiku Explore agents) *before* writing specs or making non-trivial changes — the main thread then writes from verified facts instead of impressions. This is how `docs/REQUIREMENTS.md` was produced and reviewed.
