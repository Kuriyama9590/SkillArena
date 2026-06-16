"""Dev launcher: starts FastAPI backend + Vite dev server concurrently.

Usage:
    python scripts/dev.py          # start both (recommended)
    python scripts/dev.py --build  # build frontend only

Access:
    - Frontend: http://localhost:5173  (dev server with HMR)
    - API Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

PROC_FRONTEND: subprocess.Popen | None = None
PROC_BACKEND: subprocess.Popen | None = None


def _log(tag: str, msg: str) -> None:
    print(f"\x1b[1;34m[{tag}]\x1b[0m {msg}", flush=True)


def _kill_port(port: int) -> None:
    """Kill any process using the given port (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.pid:
                try:
                    p = psutil.Process(conn.pid)
                    p.terminate()
                    p.wait(timeout=2)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                    pass
    except ImportError:
        # fallback: netstat + taskkill
        try:
            result = subprocess.run(
                ["cmd", "/c", f"for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :{port}') do taskkill /F /PID %a"],
                capture_output=True,
                text=True,
            )
        except Exception:
            pass


def _stream(proc: subprocess.Popen, prefix: str, color: str) -> None:
    """Stream subprocess stdout/stderr to terminal with prefix."""
    colors = {
        "green": "32",
        "cyan": "36",
        "yellow": "33",
        "magenta": "35",
    }
    c = colors.get(color, "0")
    try:
        for line in proc.stdout:  # type: ignore[arg-type]
            if line:
                sys.stdout.write(f"\x1b[{c}m[{prefix}]\x1b[0m {line}")
                sys.stdout.flush()
    except Exception:
        pass


def start_backend(port: int) -> subprocess.Popen:
    _kill_port(port)
    time.sleep(0.5)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "backend.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--reload",
            "--log-level", "info",
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    return proc


def start_frontend() -> subprocess.Popen:
    proc = subprocess.Popen(
        ["cmd", "/c", "npm run dev -- --port 5173"],
        cwd=str(FRONTEND),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    return proc


def shutdown() -> None:
    _log("dev", "Shutting down...")
    for proc, name in [(PROC_BACKEND, "backend"), (PROC_FRONTEND, "frontend")]:
        if proc is None:
            continue
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGINT)
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    _log("dev", "All stopped.")


def main() -> int:
    global PROC_BACKEND, PROC_FRONTEND

    parser = argparse.ArgumentParser(description="Skill Arena dev launcher")
    parser.add_argument("--build", action="store_true", help="Build frontend only")
    parser.add_argument("--port", type=int, default=8000, help="Backend port")
    parser.add_argument("--no-frontend", action="store_true", help="Backend only")
    args = parser.parse_args()

    if args.build:
        _log("dev", "Building frontend...")
        subprocess.run(
            ["cmd", "/c", "npm run build"],
            cwd=str(FRONTEND),
            check=True,
        )
        _log("dev", "Frontend built to frontend/dist/")
        return 0

    print("\n" + "=" * 60)
    print("  Skill Arena Dev Server")
    print("=" * 60)

    if not args.no_frontend:
        _log("dev", "Starting backend + frontend...")
    else:
        _log("dev", "Starting backend only...")

    PROC_BACKEND = start_backend(args.port)
    threading.Thread(target=_stream, args=(PROC_BACKEND, "API", "green"), daemon=True).start()

    if not args.no_frontend:
        time.sleep(1.5)  # let backend start first
        PROC_FRONTEND = start_frontend()
        threading.Thread(target=_stream, args=(PROC_FRONTEND, "WEB", "cyan"), daemon=True).start()

    if not args.no_frontend:
        time.sleep(2)
        print("\n" + "-" * 60)
        print(f"  Frontend: \x1b[36mhttp://localhost:5173\x1b[0m")
        print(f"  API Docs: \x1b[32mhttp://127.0.0.1:{args.port}/docs\x1b[0m")
        print("  Press Ctrl+C to stop")
        print("-" * 60 + "\n")
    else:
        print(f"\n  API: http://127.0.0.1:{args.port}")
        print("  Press Ctrl+C to stop\n")

    try:
        PROC_BACKEND.wait()
    except KeyboardInterrupt:
        shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
