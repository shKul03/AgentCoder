"""BrainUpdater — appends dated memory entries to agent brain.md files.

Phase 3 of the Agent OS expansion: Brain.md Memory System.

After each agent completes its task, the orchestrator calls
``BrainUpdater.update(...)`` which:

1. Builds a short structured summary (via a cheap LLM call OR a
   rule-based fallback if no API key is available).
2. Appends a dated entry to the agent's brain.md through
   ``AgentIdentityStore.append_to_brain``.
3. Enforces a MAX_BRAIN_CHARS limit; when exceeded it runs a
   compression pass that condenses older entries into one historical
   block so recent memory remains sharp and token cost stays bounded.

Usage (from handlers.py)::

    from agent_os.agents.brain import BrainUpdater

    brain = BrainUpdater(config)
    brain.update(
        post="MODULE_MAKER",
        task_summary="Decomposed 7 epics into 12 modules",
        output_summary="Produced mod-0 … mod-11 with full spec JSON",
        extra_context={"modules": 12, "module_id": None},
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config.schema import AgentOSConfig
from .store import AgentIdentityStore, AgentRegistry

logger = logging.getLogger(__name__)

# Max characters to keep in brain.md before a compression pass.
# ~50 000 chars ≈ ~12 500 tokens at 4 chars/token — well within context windows.
MAX_BRAIN_CHARS = 50_000

# Number of recent entries to keep verbatim during compression
VERBATIM_RECENT_ENTRIES = 5

_AGENTS_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Summarisation prompt templates
# ---------------------------------------------------------------------------

_SUMMARISE_SYSTEM = (
    "You are a concise technical scribe embedded inside an autonomous software-"
    "development pipeline. Your sole job is to write brief, factual memory entries "
    "so that future runs of the same agent can learn from past experience.\n\n"
    "Rules:\n"
    "- Write exactly 3–6 bullet points.\n"
    "- Be specific and concrete — name actual module IDs, file counts, error types.\n"
    "- Do NOT repeat information that is already obvious from the task type.\n"
    "- Do NOT include any preamble, heading, or trailing commentary.\n"
    "- Start each bullet with `- `."
)

_SUMMARISE_USER = (
    "Agent post: {post}\n"
    "Task completed: {task_summary}\n"
    "Output produced: {output_summary}\n"
    "Extra context: {extra_context}\n\n"
    "Write a 3–6 bullet memory entry for this agent's brain.md."
)

_COMPRESSION_SYSTEM = (
    "You are compressing a long memory log into a compact historical summary. "
    "Preserve the most important facts, patterns, and recurring issues. "
    "Write 8–15 bullet points covering all entries. No preamble, no headings."
)


class BrainUpdater:
    """Appends and manages brain.md entries for all pipeline agents."""

    def __init__(
        self,
        config: AgentOSConfig,
        agents_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._agents_dir = agents_dir or _AGENTS_DIR
        self._store = AgentIdentityStore(self._agents_dir)
        self._registry = AgentRegistry(self._agents_dir / "registry.json")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        post: str,
        task_summary: str,
        output_summary: str,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        """Append a new memory entry to the agent registered at *post*.

        Gracefully no-ops if the agent cannot be resolved or the write fails,
        so a brain update failure never interrupts the pipeline.
        """
        try:
            agent_name = self._registry.get_agent_for_post(post)
            if not agent_name:
                logger.warning("BrainUpdater: no agent for post '%s' — skipping", post)
                return

            entry = self._build_entry(
                post=post,
                task_summary=task_summary,
                output_summary=output_summary,
                extra_context=extra_context or {},
            )
            self._store.append_to_brain(agent_name, entry)
            logger.info("Brain updated for agent '%s' (post: %s)", agent_name, post)

            # Enforce size limit after appending
            self._maybe_compress(agent_name)

        except Exception:
            logger.warning(
                "BrainUpdater: failed to update brain for post '%s' (non-fatal)",
                post,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Entry building
    # ------------------------------------------------------------------

    def _build_entry(
        self,
        post: str,
        task_summary: str,
        output_summary: str,
        extra_context: dict[str, Any],
    ) -> str:
        """Try an LLM summarisation; fall back to rule-based if unavailable."""
        llm_entry = self._llm_summarise(post, task_summary, output_summary, extra_context)
        if llm_entry:
            return llm_entry
        return self._rule_based_entry(post, task_summary, output_summary, extra_context)

    def _llm_summarise(
        self,
        post: str,
        task_summary: str,
        output_summary: str,
        extra_context: dict[str, Any],
    ) -> str:
        """Call the chat completions API for a structured memory bullet list.

        Returns empty string on any failure so the caller falls back to
        rule-based entry generation.
        """
        api_key = self._config.secrets.openai_api_key
        if not api_key:
            return ""

        # Use the cheapest model in the routing table, defaulting to gpt-4.1-mini
        model = (
            self._config.codex.model_routing.get("BRAIN_UPDATER")
            or self._config.codex.model_routing.get("PROMPT_GENERATOR")
            or "gpt-4.1-mini"
        )

        user_msg = _SUMMARISE_USER.format(
            post=post,
            task_summary=task_summary,
            output_summary=output_summary,
            extra_context=str(extra_context),
        )

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=model,
                temperature=0.3,
                max_tokens=300,
                messages=[
                    {"role": "system", "content": _SUMMARISE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = resp.choices[0].message.content
            if text and text.strip():
                logger.debug("LLM brain entry for post '%s' (%d chars)", post, len(text))
                return text.strip()
        except Exception:
            logger.debug(
                "LLM summarisation failed for post '%s' — using rule-based fallback",
                post,
                exc_info=True,
            )
        return ""

    @staticmethod
    def _rule_based_entry(
        post: str,
        task_summary: str,
        output_summary: str,
        extra_context: dict[str, Any],
    ) -> str:
        """Build a plain bullet-point entry without an LLM call."""
        lines = [
            f"- **Post**: {post}",
            f"- **Task**: {task_summary}",
            f"- **Output**: {output_summary}",
        ]
        for k, v in extra_context.items():
            if v is not None:
                lines.append(f"- **{k}**: {v}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Compression
    # ------------------------------------------------------------------

    def _maybe_compress(self, agent_name: str) -> None:
        """If brain.md exceeds MAX_BRAIN_CHARS, compress older entries."""
        brain_text = self._store.get_file(agent_name, "brain.md")
        if len(brain_text) <= MAX_BRAIN_CHARS:
            return

        logger.info(
            "Brain for '%s' is %d chars — running compression pass",
            agent_name,
            len(brain_text),
        )
        compressed = self._compress(agent_name, brain_text)
        if compressed:
            self._store.update_file(agent_name, "brain.md", compressed)

    def _compress(self, agent_name: str, brain_text: str) -> str:
        """Condense older entries, keeping the most recent VERBATIM_RECENT_ENTRIES verbatim.

        The earliest portion is condensed by the LLM (or naively truncated on fallback).
        """
        import re
        # Split into individual entries
        entry_chunks = re.split(r"---\s*\n+##\s*Entry\s*—\s*", brain_text)
        header_block = entry_chunks[0].strip()  # lines before first entry
        entries = [e.strip() for e in entry_chunks[1:] if e.strip()]

        if len(entries) <= VERBATIM_RECENT_ENTRIES:
            # Nothing old enough to compress
            return ""

        old_entries = entries[:-VERBATIM_RECENT_ENTRIES]
        recent_entries = entries[-VERBATIM_RECENT_ENTRIES:]

        historical_text = self._llm_compress(agent_name, old_entries)
        if not historical_text:
            # Rule-based fallback: keep a simple "n older entries condensed" note
            historical_text = (
                f"- {len(old_entries)} older entries condensed into this historical block.\n"
                "- See git history of brain.md for full details."
            )

        # Reassemble: header → historical block → recent entries
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        parts = [
            header_block,
            f"\n\n---\n\n## Historical Context (compressed {now})\n\n{historical_text}",
        ]
        for entry in recent_entries:
            parts.append(f"\n\n---\n\n## Entry — {entry}")

        return "".join(parts)

    def _llm_compress(self, agent_name: str, old_entries: list[str]) -> str:
        api_key = self._config.secrets.openai_api_key
        if not api_key:
            return ""
        model = (
            self._config.codex.model_routing.get("BRAIN_UPDATER")
            or "gpt-4.1-mini"
        )
        combined = "\n\n---\n\n".join(old_entries)
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            resp = client.chat.completions.create(
                model=model,
                temperature=0.2,
                max_tokens=600,
                messages=[
                    {"role": "system", "content": _COMPRESSION_SYSTEM},
                    {"role": "user", "content": (
                        f"Compress these memory entries for agent '{agent_name}':\n\n"
                        f"{combined}"
                    )},
                ],
            )
            text = resp.choices[0].message.content
            return text.strip() if text else ""
        except Exception:
            logger.debug("LLM compression failed for '%s'", agent_name, exc_info=True)
            return ""
