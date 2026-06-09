"""CLI adapter — builds the subprocess command for each supported coding CLI tool.

Supported tools
---------------
codex       OpenAI Codex CLI  (default)
            ``codex exec --full-auto --skip-git-repo-check [--model M] [-c store=true] PROMPT``

aider       Aider AI pair-programmer
            ``aider --yes-always --no-git [--model M] --message PROMPT``

claude      Anthropic Claude Code CLI
            ``claude --print [--model M] PROMPT``
"""

from __future__ import annotations

import shutil

# Tools that have native CLI binaries
CLI_TOOLS: list[str] = ["codex", "aider", "claude"]

# No API-adapter tools — all routing is done via native CLI binaries or Ollama
API_TOOLS: list[str] = []

# All supported tools
SUPPORTED_TOOLS: list[str] = CLI_TOOLS + API_TOOLS

# Human-readable labels for the UI
TOOL_LABELS: dict[str, str] = {
    "codex": "Codex (OpenAI)",
    "aider": "Aider",
    "claude": "Claude Code (Anthropic)",
}

# No API tools — empty
_API_TOOL_ENV_KEYS: dict[str, str] = {}


class UnsupportedToolError(ValueError):
    """Raised when a tool key is not in SUPPORTED_TOOLS."""


def build_command(tool: str, model: str, prompt: str, working_dir: str = "",
                  *, use_stdin: bool = False) -> list[str]:
    """Return the subprocess ``argv`` list for the given CLI tool.

    Args:
        tool:        One of ``SUPPORTED_TOOLS`` (case-insensitive).
        model:       The model identifier to pass to the CLI (may be empty).
        prompt:      The task / prompt text to send.
        working_dir: Working directory path; passed to Codex as ``--add-dir``
                     so it receives explicit write access to that folder.

    Returns:
        A ``list[str]`` ready to pass to ``subprocess.Popen``.

    Raises:
        UnsupportedToolError: If *tool* is not recognized.
    """
    if not tool or not tool.strip():
        raise UnsupportedToolError(
            f"No tool specified. Supported tools: {', '.join(SUPPORTED_TOOLS)}"
        )
    tool = tool.lower().strip()

    if tool == "aider":
        cmd = ["aider", "--yes-always", "--no-git"]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["--message", prompt])
        return cmd

    if tool in ("claude", "claude-code"):
        cmd = ["claude", "--print"]
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)
        return cmd

    if tool == "codex":
        # codex exec --full-auto --skip-git-repo-check --sandbox danger-full-access
        #            [-C <working_dir>] [--add-dir <dir>] [-m <model>] <prompt>
        cmd = ["codex", "exec", "--full-auto", "--skip-git-repo-check", "--sandbox", "danger-full-access"]
        if working_dir:
            cmd.extend(["-C", working_dir])          # set working root
            cmd.extend(["--add-dir", working_dir])   # grant write access
        if model:
            cmd.extend(["-m", model])
        # Use '-' to read prompt from stdin (avoids Windows command-line length limit)
        cmd.append("-" if use_stdin else prompt)
        return cmd

    raise UnsupportedToolError(
        f"Unknown CLI tool '{tool}'. Supported tools: {', '.join(SUPPORTED_TOOLS)}"
    )


def executable_name(tool: str) -> str:
    """Return the bare executable name for ``FileNotFoundError`` error messages."""
    tool = (tool or "").lower().strip()
    if tool == "aider":
        return "aider"
    if tool in ("claude", "claude-code"):
        return "claude"
    if tool in API_TOOLS:
        return f"api_adapter({tool})"
    return tool or "unknown"


def is_tool_available(tool: str) -> bool:
    """Check whether the given tool is available for use.

    For CLI tools: checks if the binary exists on ``$PATH``.
    For API tools: checks if the required API key env var is set.
    """
    tool = (tool or "").lower().strip()

    if tool == "codex":
        return shutil.which("codex") is not None
    if tool == "aider":
        return shutil.which("aider") is not None
    if tool in ("claude", "claude-code"):
        return shutil.which("claude") is not None

    return False
