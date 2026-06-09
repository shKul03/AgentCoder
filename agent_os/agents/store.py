"""Agent Identity Store — reads and writes per-agent .md definition files.

Each agent has 5 files on disk:
  skills.md   — what the agent can do
  soul.md     — persona and behavioral qualities
  tools.md    — tools the agent has access to
  ceiling.md  — what it can/cannot do and when to escalate
  brain.md    — auto-maintained memory of past runs

Files live under agent_os/agents/{agent_name}/.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Ordered canonical file names for every agent
AGENT_FILES = ("skills.md", "soul.md", "tools.md", "ceiling.md", "brain.md")

# Built-in agent names that cannot be deleted
BUILTIN_AGENTS = frozenset({"prompt_generator", "code_generator", "code_reviewer"})

# The pipeline posts that can be remapped to any agent
PIPELINE_POSTS = ("PROMPT_GENERATOR", "CODE_GENERATOR", "CODE_REVIEWER")


class AgentNotFoundError(Exception):
    pass


class AgentIdentityStore:
    """Reads and writes agent definition .md files from disk."""

    def __init__(self, agents_dir: str | Path | None = None) -> None:
        if agents_dir is None:
            agents_dir = Path(__file__).resolve().parent
        self._root = Path(agents_dir)

    # ------------------------------------------------------------------
    # Agent discovery
    # ------------------------------------------------------------------

    def list_agents(self) -> list[dict[str, Any]]:
        """Return metadata for all agents (built-in + custom).

        Each entry: {name, display_name, is_builtin, files_present, post_assignment}
        post_assignment is resolved from the registry.
        """
        registry = AgentRegistry(self._root / "registry.json")
        post_map = registry.get_registry()  # {POST: agent_name}
        agent_to_post = {v: k for k, v in post_map.items()}

        agents = []
        for path in sorted(self._root.iterdir()):
            if not path.is_dir() or path.name in {"custom", "__pycache__", ".git", ".mypy_cache"}:
                continue
            entry = self._build_agent_meta(path, agent_to_post, is_custom=False)
            agents.append(entry)

        # custom sub-folder
        custom_dir = self._root / "custom"
        if custom_dir.is_dir():
            for path in sorted(custom_dir.iterdir()):
                if path.is_dir():
                    entry = self._build_agent_meta(path, agent_to_post, is_custom=True)
                    agents.append(entry)

        return agents

    def _build_agent_meta(
        self,
        path: Path,
        agent_to_post: dict[str, str],
        is_custom: bool,
    ) -> dict[str, Any]:
        name = path.name if not is_custom else f"custom/{path.name}"
        files_present = [f for f in AGENT_FILES if (path / f).is_file()]
        return {
            "name": name,
            "display_name": path.name.replace("_", " ").title(),
            "is_builtin": path.name in BUILTIN_AGENTS,
            "is_custom": is_custom,
            "files_present": files_present,
            "post_assignment": agent_to_post.get(name) or agent_to_post.get(path.name),
        }

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get_agent(self, agent_name: str) -> dict[str, str]:
        """Return all file contents for an agent.

        Returns dict: {filename: content}. Missing files return empty string.
        Raises AgentNotFoundError if the agent directory does not exist.
        """
        agent_dir = self._resolve_dir(agent_name)
        if not agent_dir.is_dir():
            raise AgentNotFoundError(f"Agent '{agent_name}' not found at {agent_dir}")

        result: dict[str, str] = {}
        for fname in AGENT_FILES:
            fpath = agent_dir / fname
            result[fname] = fpath.read_text(encoding="utf-8") if fpath.is_file() else ""
        return result

    def get_file(self, agent_name: str, file_name: str) -> str:
        """Read a single .md file for an agent.

        Raises AgentNotFoundError if agent doesn't exist.
        Raises ValueError if file_name is not a recognised agent file.
        """
        if file_name not in AGENT_FILES:
            raise ValueError(f"'{file_name}' is not a valid agent file. Choose from: {AGENT_FILES}")
        agent_dir = self._resolve_dir(agent_name)
        if not agent_dir.is_dir():
            raise AgentNotFoundError(f"Agent '{agent_name}' not found")
        fpath = agent_dir / file_name
        return fpath.read_text(encoding="utf-8") if fpath.is_file() else ""

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def update_file(self, agent_name: str, file_name: str, content: str) -> None:
        """Write content to a single .md file for an agent.

        Creates the agent directory if it doesn't exist (used for custom agents).
        Raises ValueError if file_name is not valid.
        """
        if file_name not in AGENT_FILES:
            raise ValueError(f"'{file_name}' is not a valid agent file. Choose from: {AGENT_FILES}")
        agent_dir = self._resolve_dir(agent_name)
        agent_dir.mkdir(parents=True, exist_ok=True)
        fpath = agent_dir / file_name
        fpath.write_text(content, encoding="utf-8")
        logger.info("Updated %s/%s (%d chars)", agent_name, file_name, len(content))

    def append_to_brain(self, agent_name: str, entry: str) -> None:
        """Append a dated entry to the agent's brain.md."""
        from datetime import datetime
        agent_dir = self._resolve_dir(agent_name)
        agent_dir.mkdir(parents=True, exist_ok=True)
        brain_path = agent_dir / "brain.md"

        header = f"\n\n---\n\n## Entry — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        text = header + entry.strip()

        if brain_path.is_file():
            existing = brain_path.read_text(encoding="utf-8")
            # Remove the "no entries yet" placeholder on first real entry
            if "No entries yet" in existing:
                existing = existing.split("---")[0].rstrip()
            brain_path.write_text(existing + text, encoding="utf-8")
        else:
            # Create fresh brain with standard header
            brain_path.write_text(
                f"# {agent_name.replace('_', ' ').title()} — Brain\n\n"
                "*This file is automatically maintained by Agent OS.*\n"
                + text,
                encoding="utf-8",
            )
        logger.info("Appended brain entry for agent '%s'", agent_name)

    # ------------------------------------------------------------------
    # Agent lifecycle
    # ------------------------------------------------------------------

    def create_agent(self, agent_name: str, files: dict[str, str]) -> None:
        """Create a new custom agent with the provided file contents.

        agent_name is stored under agent_os/agents/custom/{agent_name}/.
        Raises ValueError if the name conflicts with a built-in or already exists.
        """
        if agent_name in BUILTIN_AGENTS:
            raise ValueError(f"'{agent_name}' is a reserved built-in agent name")

        agent_dir = self._root / "custom" / agent_name
        if agent_dir.is_dir():
            raise ValueError(f"Custom agent '{agent_name}' already exists")

        agent_dir.mkdir(parents=True, exist_ok=True)
        for fname in AGENT_FILES:
            content = files.get(fname, "")
            (agent_dir / fname).write_text(content, encoding="utf-8")

        logger.info("Created custom agent '%s' at %s", agent_name, agent_dir)

    def delete_agent(self, agent_name: str) -> None:
        """Delete a custom agent directory.

        Accepts names as either 'my_agent' or 'custom/my_agent'.
        Raises ValueError if attempting to delete a built-in agent.
        Raises AgentNotFoundError if the agent doesn't exist.
        """
        bare_name = agent_name.removeprefix("custom/")
        if bare_name in BUILTIN_AGENTS:
            raise ValueError(f"Cannot delete built-in agent '{bare_name}'")

        agent_dir = self._root / "custom" / bare_name
        if not agent_dir.is_dir():
            raise AgentNotFoundError(f"Custom agent '{agent_name}' not found")

        import shutil
        shutil.rmtree(agent_dir)
        logger.info("Deleted custom agent '%s'", agent_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_dir(self, agent_name: str) -> Path:
        """Resolve the directory for an agent name (e.g. 'prompt_generator', 'code_generator')."""
        if agent_name.startswith("custom/"):
            return self._root / agent_name
        return self._root / agent_name


class AgentRegistry:
    """Maps pipeline posts to agent directory names.

    Backed by registry.json. Thread-safe for reads; writes are protected
    by a simple file-level replace.
    """

    def __init__(self, registry_path: str | Path | None = None) -> None:
        if registry_path is None:
            registry_path = Path(__file__).resolve().parent / "registry.json"
        self._path = Path(registry_path)

    def get_registry(self) -> dict[str, str]:
        """Return the full {post: agent_name} mapping."""
        if not self._path.is_file():
            return self._default_registry()
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read registry.json: %s — using defaults", exc)
            return self._default_registry()

    def get_agent_for_post(self, post: str) -> str:
        """Return the agent name assigned to a pipeline post."""
        return self.get_registry().get(post, self._default_registry().get(post, ""))

    def update_registry(self, mapping: dict[str, str]) -> None:
        """Write a full or partial registry update.

        Only keys in PIPELINE_POSTS are accepted; unknown keys are ignored.
        """
        current = self.get_registry()
        for post, agent in mapping.items():
            if post in PIPELINE_POSTS:
                current[post] = agent
            else:
                logger.warning("Ignored unknown post key in registry update: %s", post)
        self._path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        logger.info("Registry updated: %s", current)

    @staticmethod
    def _default_registry() -> dict[str, str]:
        return {
            "PROMPT_GENERATOR": "prompt_generator",
            "CODE_GENERATOR": "code_generator",
            "CODE_REVIEWER": "code_reviewer",
        }
