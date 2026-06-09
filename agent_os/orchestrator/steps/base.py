"""Shared context object passed to every step function."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ...config.schema import AgentOSConfig
from ...storage.database import Database
from ...vcs.factory import make_vcs_client
from ..state import StateManager


@dataclass
class StepContext:
    """Carries all shared resources a step function needs."""

    config: AgentOSConfig
    db: Database
    state_mgr: StateManager
    ws_queue: Optional[asyncio.Queue]  # None when running without WebSocket
    _emit_fn: Callable[..., None] = field(repr=False)
    _emit_terminal_fn: Callable[..., None] = field(repr=False)
    _upsert_iteration_fn: Callable[..., None] = field(repr=False)

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self._emit_fn(event_type, data)

    def emit_terminal(
        self,
        event_type: str,
        agent_post: str,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        self._emit_terminal_fn(event_type, agent_post, session_id, **kwargs)

    def upsert_iteration(self, iteration_number: int, **kwargs: Any) -> None:
        self._upsert_iteration_fn(iteration_number, **kwargs)

    def new_session_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    def make_project_vcs(self) -> Any:
        """Return a VCS client scoped to the actual project repo."""
        vcs = make_vcs_client(self.config)
        if vcs is None:
            return None
        repo = self.config.project.repo_name or ""
        if repo and hasattr(vcs, "for_repo") and repo != getattr(vcs, "_repo", ""):
            return vcs.for_repo(repo)
        return vcs
