"""CLI entry point for Agent OS orchestrator."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

from .engine import Orchestrator

console = Console()


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Agent OS — Autonomous SDLC Engine",
        prog="agent-os",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset pipeline state to IDLE",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current pipeline status and exit",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve the current HITL gate",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run in auto-pilot mode (auto-approve all HITL gates)",
    )
    parser.add_argument(
        "--with-api",
        action="store_true",
        help="Start pipeline + FastAPI + frontend server",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    """CLI entry point for Agent OS orchestrator."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )

    # Load config
    from ..config.loader import load_config

    config_path = Path(args.config)
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        sys.exit(1)

    config = load_config(config_path)

    if args.auto:
        config.orchestrator.auto_approve_hitl = True

    orchestrator = Orchestrator(config)

    try:
        if args.reset:
            orchestrator.state_mgr.reset()
            console.print("[green]Pipeline state reset to IDLE.[/green]")
        elif args.status:
            console.print(orchestrator.get_status_table())
        elif args.approve:
            if orchestrator.approve_gate():
                console.print("[green]Gate approved. Run again to continue.[/green]")
            else:
                console.print("[red]Not at a HITL gate or approval failed.[/red]")
        else:
            orchestrator.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. State preserved — resume by running again.[/yellow]")
    finally:
        orchestrator.shutdown()


if __name__ == "__main__":
    main()
