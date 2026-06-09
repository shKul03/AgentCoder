"""CLI adapter — builds the subprocess command for each supported coding CLI tool.

Supported tools
---------------
codex       OpenAI Codex CLI  (default)
            ``codex exec --full-auto --skip-git-repo-check [--model M] [-c store=true] PROMPT``

aider       Aider AI pair-programmer
            ``aider --yes-always --no-git [--model M] --message PROMPT``

claude      Anthropic Claude Code CLI
            ``claude --print [--model M] PROMPT``

gemini      Via OpenAI-compatible API adapter (``api_adapter.py``)
qwen        Via OpenAI-compatible API adapter (``api_adapter.py``)
deepseek    Via OpenAI-compatible API adapter (``api_adapter.py``)
copilot     Via OpenAI-compatible API adapter (``api_adapter.py``)
"""

from __future__ import annotations

import os
import shutil
import sys

# Tools that have native CLI binaries
CLI_TOOLS: list[str] = ["codex", "aider", "claude"]

# Tools that use the unified OpenAI-compatible api_adapter.py
API_TOOLS: list[str] = ["gemini", "qwen", "deepseek", "copilot"]

# All supported tools
SUPPORTED_TOOLS: list[str] = CLI_TOOLS + API_TOOLS

# Human-readable labels for the UI
TOOL_LABELS: dict[str, str] = {
    "codex": "Codex (OpenAI)",
    "aider": "Aider",
    "claude": "Claude Code (Anthropic)",
    "gemini": "Gemini (Google)",
    "qwen": "Qwen Coder (Alibaba)",
    "deepseek": "DeepSeek Coder",
    "copilot": "GitHub Copilot",
}

# Map of API tools → their required env var for credential checks
_API_TOOL_ENV_KEYS: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "copilot": "GITHUB_TOKEN",
}


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

    if tool in API_TOOLS:
        # Route through the unified api_adapter as a subprocess.
        # When use_stdin=True, pass '-' so the adapter reads the prompt from stdin
        # (avoids the Windows 8191-char command-line length limit for large prompts).
        cmd = [
            sys.executable, "-m", "agent_os.codex.api_adapter",
            "--tool", tool,
        ]
        if not use_stdin:
            cmd.extend(["--prompt", prompt])
        if model:
            cmd.extend(["--model", model])
        if use_stdin:
            cmd.append("--stdin")
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

    # API tools — check for credentials
    env_key = _API_TOOL_ENV_KEYS.get(tool, "")
    if env_key:
        val = os.environ.get(env_key, "")
        return bool(val and not val.startswith("***"))

    return False
