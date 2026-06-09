"""Dependency injection for the Agent OS API.

Provides a shared Orchestrator instance and derived helpers.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from ..config.schema import AgentOSConfig
from ..orchestrator.engine import Orchestrator


class _OrchestratorHolder:
    """Singleton container for the Orchestrator instance."""

    def __init__(self) -> None:
        self._orch: Optional[Orchestrator] = None
        self._lock = threading.Lock()
        # Absolute path to the config.yaml that was loaded at startup.
        # None when the orchestrator was initialised in-memory (e.g. tests).
        self.config_path: Optional[Path] = None

    def init(self, config: AgentOSConfig) -> Orchestrator:
        with self._lock:
            if self._orch is None:
                self._orch = Orchestrator(config)
            return self._orch

    @property
    def orchestrator(self) -> Orchestrator:
        if self._orch is None:
            raise RuntimeError("Orchestrator not initialised — call init() first")
        return self._orch

    def shutdown(self) -> None:
        with self._lock:
            if self._orch is not None:
                self._orch.shutdown()
                self._orch = None
            self.config_path = None


orch_holder = _OrchestratorHolder()


def get_orchestrator() -> Orchestrator:
    """FastAPI dependency — returns the shared Orchestrator."""
    return orch_holder.orchestrator
