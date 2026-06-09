"""IdentityContextInjector — loads agent identity files and builds prompt prefixes.

Phase 2 of the Agent OS expansion: context injection into runner prompts.

Usage::

    injector = IdentityContextInjector("CODE_REVIEWER")
    preamble = injector.build_preamble()
    full_prompt = preamble + existing_prompt

The injector is intentionally defensive — if files are missing or the
registry cannot be read, every method returns an empty string so the runner
falls back to its existing hardcoded prompts transparently.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .store import AgentIdentityStore, AgentRegistry

logger = logging.getLogger(__name__)

_AGENTS_DIR = Path(__file__).resolve().parent


class IdentityContextInjector:
    """Loads agent identity files for a pipeline post and builds context blocks.

    The injector is lazy — files are only read on first access and cached
    for the lifetime of the object.
    """

    def __init__(self, post: str, agents_dir: Path | None = None) -> None:
        self._post = post
        self._agents_dir = agents_dir or _AGENTS_DIR
        self._files: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # Lazy loader
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, str]:
        """Load all identity files for this post's agent. Cached after first call."""
        if self._files is not None:
            return self._files

        try:
            registry = AgentRegistry(self._agents_dir / "registry.json")
            agent_name = registry.get_agent_for_post(self._post)
            if not agent_name:
                logger.warning(
                    "No agent registered for post '%s' — identity context disabled",
                    self._post,
                )
                self._files = {}
                return self._files

            store = AgentIdentityStore(self._agents_dir)
            self._files = store.get_agent(agent_name)
            logger.debug(
                "Loaded identity context for post '%s' (agent: %s)",
                self._post,
                agent_name,
            )
        except Exception:
            logger.warning(
                "Could not load identity context for post '%s' — falling back to defaults",
                self._post,
                exc_info=True,
            )
            self._files = {}

        return self._files

    # ------------------------------------------------------------------
    # Individual file accessors
    # ------------------------------------------------------------------

    def soul(self) -> str:
        """Return the agent's soul.md content (persona and behavioral qualities)."""
        return self._load().get("soul.md", "")

    def skills(self) -> str:
        """Return the agent's skills.md content (capabilities)."""
        return self._load().get("skills.md", "")

    def tools(self) -> str:
        """Return the agent's tools.md content (tool inventory)."""
        return self._load().get("tools.md", "")

    def ceiling(self) -> str:
        """Return the agent's ceiling.md content (permissions and hard limits)."""
        return self._load().get("ceiling.md", "")

    def recent_brain(self, max_entries: int = 3) -> str:
        """Return the most recent N dated entries from brain.md.

        Returns an empty string if brain.md has no real entries yet.
        """
        brain_content = self._load().get("brain.md", "")
        if not brain_content or "No entries yet" in brain_content:
            return ""

        # Each logged entry is separated by the pattern written by append_to_brain:
        # "---\n\n## Entry — YYYY-MM-DD HH:MM UTC"
        entry_chunks = re.split(r"---\s*\n+##\s*Entry\s*—\s*", brain_content)
        real_entries = [e.strip() for e in entry_chunks[1:] if e.strip()]
        if not real_entries:
            return ""

        recent = real_entries[-max_entries:]
        # Re-assemble with the header prefix for readability
        lines = ["## Memory — recent run summaries\n"]
        for entry in recent:
            lines.append(f"### Entry — {entry}")
        return "\n\n".join(lines)

    # ------------------------------------------------------------------
    # Preamble builders
    # ------------------------------------------------------------------

    def build_preamble(
        self,
        include_brain: bool = True,
        max_brain_entries: int = 3,
    ) -> str:
        """Build a full identity context block to prepend to any prompt.

        Includes soul (persona), skills (capabilities), ceiling (constraints),
        and optionally recent brain entries. Returns an empty string if no
        meaningful content is available so callers can skip it cleanly.
        """
        files = self._load()
        if not files:
            return ""

        sections: list[str] = [
            "# Agent Identity Context\n"
            "> The following defines who you are, what you are capable of, "
            "and the hard boundaries of your authority.\n"
        ]

        soul_text = self.soul().strip()
        if soul_text:
            sections.append(f"## Role & Persona\n\n{soul_text}")

        skills_text = self.skills().strip()
        if skills_text:
            sections.append(f"## Capabilities\n\n{skills_text}")

        tools_text = self.tools().strip()
        if tools_text:
            sections.append(f"## Tools I Have Access To\n\n{tools_text}")

        ceiling_text = self.ceiling().strip()
        if ceiling_text:
            sections.append(f"## Constraints & Boundaries\n\n{ceiling_text}")

        if include_brain:
            brain_text = self.recent_brain(max_brain_entries)
            if brain_text:
                sections.append(brain_text)

        # Only the header was added — nothing useful
        if len(sections) <= 1:
            return ""

        return "\n\n".join(sections) + "\n\n---\n\n"

    def build_role_preamble(self) -> str:
        """Build a compact soul + skills block for injecting into chat system prompts.

        Used by Prompt Generator where the system‑prompt is an LLM message,
        not a Codex CLI free-text prompt.
        """
        files = self._load()
        if not files:
            return ""

        parts: list[str] = []

        soul_text = self.soul().strip()
        if soul_text:
            parts.append(soul_text)

        skills_text = self.skills().strip()
        if skills_text:
            parts.append(skills_text)

        return "\n\n".join(parts)
