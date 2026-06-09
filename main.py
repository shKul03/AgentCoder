"""Development server launcher for Agent OS.

Runs uvicorn with hot-reload scoped ONLY to the API layer (agent_os/api/).
This prevents uvicorn StatReload from restarting the server — and killing
any running Codex pipeline — when files in storage/, orchestrator/, or
codex/ are written during a pipeline run.

Usage:
    python main.py [--port 8000] [--no-reload]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Agent OS API server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-reload", action="store_true", help="Disable hot-reload")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    # Only watch the API routes folder — never the storage/orchestrator/codex
    # directories that are written to during active pipeline runs.
    api_dir = str(root / "agent_os" / "api")

    uvicorn.run(
        "agent_os.api.app:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        reload_dirs=[api_dir],
    )


if __name__ == "__main__":
    main()
