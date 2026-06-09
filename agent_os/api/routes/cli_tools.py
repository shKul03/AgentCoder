"""CLI-tool management routes — detect, check auth, login, logout."""

from __future__ import annotations

import asyncio
import base64 as _b64
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cli-tools", tags=["cli-tools"])

# ── OS detection ─────────────────────────────────────────────────────────────
_IS_WINDOWS = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"


# ── Tool registry ────────────────────────────────────────────────────────────

class _ToolMeta:
    """Static metadata for each supported CLI tool."""

    def __init__(
        self,
        key: str,
        display_name: str,
        binary: str,
        install_cmd: str,
        docs_url: str,
        auth_check_cmd: list[str],
        login_cmd: list[str],
        logout_cmd: list[str],
        env_key: str | None = None,
        alt_binaries: list[str] | None = None,
    ):
        self.key = key
        self.display_name = display_name
        self.binary = binary
        self.alt_binaries = alt_binaries or []
        self.install_cmd = install_cmd
        self.docs_url = docs_url
        self.auth_check_cmd = auth_check_cmd
        self.login_cmd = login_cmd
        self.logout_cmd = logout_cmd
        self.env_key = env_key


TOOL_REGISTRY: dict[str, _ToolMeta] = {
    "codex": _ToolMeta(
        key="codex",
        display_name="OpenAI Codex CLI",
        binary="codex",
        install_cmd="npm install -g @openai/codex",
        docs_url="https://github.com/openai/codex",
        auth_check_cmd=["codex", "--version"],
        login_cmd=["codex", "auth", "login"],
        logout_cmd=["codex", "auth", "logout"],
        env_key="OPENAI_API_KEY",
    ),
    "claude": _ToolMeta(
        key="claude",
        display_name="Claude Code CLI",
        binary="claude",
        install_cmd="npm install -g @anthropic-ai/claude-code",
        docs_url="https://docs.anthropic.com/en/docs/claude-code",
        auth_check_cmd=["claude", "--version"],
        login_cmd=["claude", "auth", "login"],
        logout_cmd=["claude", "auth", "logout"],
        env_key="ANTHROPIC_API_KEY",
    ),
    "gemini": _ToolMeta(
        key="gemini",
        display_name="Gemini CLI",
        binary="gemini",
        install_cmd="npm install -g @google/gemini-cli",
        docs_url="https://github.com/google-gemini/gemini-cli",
        auth_check_cmd=["gemini", "--version"],
        login_cmd=["gemini", "auth", "login"],
        logout_cmd=["gemini", "auth", "logout"],
        env_key="GEMINI_API_KEY",
        alt_binaries=["gemini-cli"],
    ),
    "qwen": _ToolMeta(
        key="qwen",
        display_name="Qwen Coder CLI",
        binary="qwen",
        install_cmd="npm install -g @qwen-code/qwen-code",
        docs_url="https://github.com/QwenLM/qwen-code",
        auth_check_cmd=["qwen", "auth", "status"],
        login_cmd=["qwen", "auth", "qwen-oauth"],   # default; coding-plan dispatched in route
        logout_cmd=[],                                # qwen has no logout subcommand
        env_key="DASHSCOPE_API_KEY",
        alt_binaries=["qwen-coder"],
    ),
    "deepseek": _ToolMeta(
        key="deepseek",
        display_name="DeepSeek CLI",
        binary="deepseek",
        install_cmd="pip install deepseek-cli",
        docs_url="https://github.com/deepseek-ai/DeepSeek-Coder",
        auth_check_cmd=["deepseek", "--version"],
        login_cmd=["deepseek", "auth", "login"],
        logout_cmd=["deepseek", "auth", "logout"],
        env_key="DEEPSEEK_API_KEY",
    ),
    "cursor": _ToolMeta(
        key="cursor",
        display_name="Cursor CLI",
        binary="cursor",
        install_cmd=(
            "winget install Anysphere.Cursor"
            if _IS_WINDOWS
            else "brew install --cask cursor"
            if _IS_MAC
            else "flatpak install flathub com.cursor.Cursor"
        ),
        docs_url="https://docs.cursor.com",
        auth_check_cmd=["cursor", "--version"],
        login_cmd=["cursor", "auth", "login"],
        logout_cmd=["cursor", "auth", "logout"],
    ),
    "copilot": _ToolMeta(
        key="copilot",
        display_name="GitHub Copilot CLI",
        binary="gh",
        install_cmd=(
            "winget install GitHub.cli ; gh extension install github/gh-copilot"
            if _IS_WINDOWS
            else "brew install gh && gh extension install github/gh-copilot"
        ),
        docs_url="https://docs.github.com/en/copilot/github-copilot-in-the-cli",
        auth_check_cmd=["gh", "auth", "status"],
        login_cmd=["gh", "auth", "login", "--web"],
        logout_cmd=["gh", "auth", "logout"],
    ),
}


# ── Response models ──────────────────────────────────────────────────────────

class ToolStatusResponse(BaseModel):
    key: str
    display_name: str
    installed: bool
    install_cmd: str
    docs_url: str
    authenticated: bool
    auth_user: str  # e.g. email or account name
    auth_method: str  # detected method
    env_configured: bool  # env-var / .env has an API key
    available: bool  # True if this tool can actually be invoked (binary on PATH or API creds set)
    error: str


class AllToolsStatusResponse(BaseModel):
    tools: list[ToolStatusResponse]


class ToolActionRequest(BaseModel):
    auth_method: str = ""
    api_key: str = ""


class ToolActionResponse(BaseModel):
    success: bool
    message: str
    auth_user: str = ""
    requires_browser: bool = False
    browser_url: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _which(binary: str) -> str | None:
    """Return absolute path, or None if not on PATH."""
    return shutil.which(binary)


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1"},
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", "Command timed out"
    except Exception as exc:
        return -3, "", str(exc)


def _run_no_gh_token(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Like _run but strips GITHUB_TOKEN / GH_TOKEN from the subprocess env.

    Used for gh auth status / gh auth login so we check the stored OAuth
    credential rather than the env-var PAT that is only meant for git operations.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
    env["NO_COLOR"] = "1"
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", "Command timed out"
    except Exception as exc:
        return -3, "", str(exc)


def _open_in_terminal(cmd: str) -> None:
    """
    Open a command in the native terminal emulator so it runs interactively.
    Supports macOS (Terminal.app), Windows (cmd / WT), and Linux.
    Prepends source of shell rc files so env vars are available.
    """
    # Source the user's shell profile so exports (API keys etc.) are available
    shell_init = "source ~/.zshrc 2>/dev/null; source ~/.bashrc 2>/dev/null; "

    if sys.platform == "darwin":
        full_cmd = shell_init + cmd
        # Escape for embedding inside a double-quoted AppleScript string:
        # backslash → \\, double-quote → \", dollar → \$
        safe = full_cmd.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$')
        # Use `do script` only — no profile manipulation (avoids AppleScript
        # errors on machines where "Basic" is renamed or a different locale).
        # `do script` in a new window is the default when no window arg is given.
        script = (
            'tell application "Terminal"\n'
            f'  do script "{safe}"\n'
            '  activate\n'
            'end tell'
        )
        subprocess.Popen(["osascript", "-e", script])

    elif sys.platform == "win32":
        full_cmd = cmd  # Windows: no bash profile sourcing
        # Encode the command as Base64 UTF-16LE so all inner quotes/special chars
        # survive subprocess argument passing without any escaping issues.
        import base64 as _b64
        encoded = _b64.b64encode(full_cmd.encode("utf-16-le")).decode("ascii")
        ps_args = ["powershell.exe", "-NoExit", "-EncodedCommand", encoded]
        if _which("wt"):
            subprocess.Popen(["wt", "--"] + ps_args, shell=False)
        else:
            subprocess.Popen(ps_args, shell=False)

    elif sys.platform.startswith("linux"):
        full_cmd = shell_init + cmd
        for term in ["gnome-terminal", "xterm", "konsole", "xfce4-terminal"]:
            if _which(term):
                if term == "gnome-terminal":
                    subprocess.Popen([term, "--", "bash", "-c", full_cmd])
                else:
                    subprocess.Popen([term, "-e", f"bash -c '{full_cmd}; exec bash'"])
                break


def _check_env_key(key: str | None) -> bool:
    """Check whether an API key is set via environment variable."""
    if not key:
        return False
    val = os.environ.get(key, "")
    return bool(val and not val.startswith("***"))


def _detect_auth(meta: _ToolMeta) -> tuple[bool, str, str]:
    """
    Best-effort detection of current auth state for a CLI tool.
    Returns (authenticated, user_display, detected_method).

    Rules:
    - Being *installed* is NOT the same as being *authenticated*.
    - Only return True when we have real evidence of a stored credential.
    """
    # 1. Check env-var API key first (fastest, works for all tools)
    if meta.env_key and _check_env_key(meta.env_key):
        return True, f"via ${meta.env_key}", "api_key"

    # 2. Per-tool auth detection
    if meta.key == "copilot":
        # Run gh auth status WITHOUT GITHUB_TOKEN / GH_TOKEN in the environment.
        # When those env vars are present, gh uses them for API calls but reports
        # no stored OAuth credential (exit code 1) — causing a false "not authenticated".
        # Stripping them here lets us check whether a real gh OAuth session exists.
        rc, out, err = _run_no_gh_token(["gh", "auth", "status"])
        if rc == 0:
            for line in (out + "\n" + err).splitlines():
                if "account" in line.lower():
                    parts = line.strip().split()
                    if parts:
                        return True, parts[-1].strip("()"), "oauth"
            return True, "GitHub account", "oauth"
        return False, "", ""

    if meta.key == "codex":
        # ~/.codex/auth.json contains {"auth_mode": ..., "OPENAI_API_KEY": "sk-..."}
        # File existing alone is not enough — the key inside must be non-empty.
        codex_auth = os.path.expanduser("~/.codex/auth.json")
        if os.path.exists(codex_auth):
            try:
                import json
                with open(codex_auth) as f:
                    data = json.load(f)
                api_key = data.get("OPENAI_API_KEY", "")
                if api_key and not api_key.startswith("***"):
                    return True, "OpenAI account", data.get("auth_mode", "account")
            except Exception:
                pass
        return False, "", ""

    if meta.key == "claude":
        # Use the CLI's own status command — most reliable
        rc, out, err = _run(["claude", "auth", "status"])
        combined = (out + "\n" + err).lower()
        if rc == 0 and ("logged in" in combined or "authenticated" in combined):
            # Try to extract email/account name
            for line in (out + "\n" + err).splitlines():
                if "@" in line or "account" in line.lower():
                    return True, line.strip(), "account"
            return True, "Anthropic account", "account"
        # Fallback: dedicated OAuth credentials file (not settings)
        claude_creds = os.path.expanduser("~/.claude/credentials.json")
        if os.path.exists(claude_creds):
            try:
                import json
                with open(claude_creds) as f:
                    data = json.load(f)
                if data.get("access_token") or data.get("oauth_token"):
                    return True, "Anthropic account", "oauth"
            except Exception:
                pass
        return False, "", ""

    if meta.key == "gemini":
        # Gemini CLI has no `auth status` subcommand — use file detection only.
        # Primary credential file: ~/.gemini/oauth_creds.json
        # Active account email:    ~/.gemini/google_accounts.json
        import json
        creds_path = os.path.expanduser("~/.gemini/oauth_creds.json")
        if os.path.exists(creds_path):
            try:
                with open(creds_path) as f:
                    data = json.load(f)
                if data.get("access_token") or data.get("refresh_token"):
                    # Try to get the email from the accounts file
                    email = ""
                    accounts_path = os.path.expanduser("~/.gemini/google_accounts.json")
                    if os.path.exists(accounts_path):
                        try:
                            with open(accounts_path) as af:
                                acc = json.load(af)
                            email = acc.get("active", "")
                        except Exception:
                            pass
                    return True, email or "Google account", "oauth"
            except Exception:
                pass
        # Fallback paths for older Gemini CLI versions
        for token_path in [
            "~/.gemini/oauth_token.json",
            "~/.config/gemini/oauth_token.json",
            "~/.config/gemini/credentials.json",
        ]:
            p = os.path.expanduser(token_path)
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        data = json.load(f)
                    if data.get("access_token") or data.get("token") or data.get("refresh_token"):
                        return True, "Google account", "oauth"
                except Exception:
                    pass
        return False, "", ""

    if meta.key == "qwen":
        rc, out, err = _run(["qwen", "auth", "status"])
        combined = (out + "\n" + err).lower()
        if rc == 0 and (
            "authenticated" in combined
            or "logged in" in combined
            or "qwen-oauth" in combined
            or "coding-plan" in combined
        ):
            method = "coding-plan" if "coding-plan" in combined else "qwen-oauth"
            return True, "Alibaba account", method
        return False, "", ""

    if meta.key == "cursor":
        # Cursor stores auth in a system keychain / app data directory
        for token_path in [
            "~/Library/Application Support/Cursor/User/globalStorage/cursor.cursor/auth.json",
            "~/.config/cursor/auth.json",
        ]:
            p = os.path.expanduser(token_path)
            if os.path.exists(p):
                try:
                    import json
                    with open(p) as f:
                        data = json.load(f)
                    if data.get("accessToken") or data.get("token") or data.get("email"):
                        user = data.get("email", "Cursor account")
                        return True, user, "account"
                except Exception:
                    pass
        return False, "", ""

    if meta.key == "deepseek":
        # DeepSeek CLI only has env-var auth (already checked above)
        return False, "", ""

    # No handler matched — do NOT fall back to running --version
    # (binary being present ≠ authenticated)
    return False, "", ""


def _get_tool_status(meta: _ToolMeta) -> ToolStatusResponse:
    """Build full status for one tool."""
    # Check installation
    path = _which(meta.binary)
    if not path:
        for alt in meta.alt_binaries:
            path = _which(alt)
            if path:
                break

    installed = path is not None
    authenticated = False
    auth_user = ""
    auth_method = ""
    env_configured = _check_env_key(meta.env_key)
    error = ""

    if installed:
        try:
            authenticated, auth_user, auth_method = _detect_auth(meta)
        except Exception as exc:
            error = str(exc)
    else:
        error = f"{meta.display_name} is not installed on this system."

    # Compute availability from the cli_adapter (binary on PATH or API creds set)
    from ...codex.cli_adapter import is_tool_available
    available = is_tool_available(meta.key)

    return ToolStatusResponse(
        key=meta.key,
        display_name=meta.display_name,
        installed=installed,
        install_cmd=meta.install_cmd,
        docs_url=meta.docs_url,
        authenticated=authenticated,
        auth_user=auth_user,
        auth_method=auth_method,
        env_configured=env_configured,
        available=available,
        error=error,
    )


# ── Routes ───────────────────────────────────────────────────────────────────

class OpenTerminalRequest(BaseModel):
    command: str


# ── Copilot available-models endpoint ────────────────────────────────────────

import re as _re

# GitHub Copilot's own model catalog (requires Copilot OAuth token or PAT with copilot scope).
_COPILOT_API_ENDPOINT   = "https://api.githubcopilot.com/models"
# GitHub AI Models marketplace (full catalog — we filter it down, works with any PAT).
_GITHUB_MODELS_ENDPOINT = "https://models.inference.ai.azure.com/models"

# Authoritative list of chat-completion models available on GitHub Copilot
# Free / Student / Pro as of 2026-05.  This is ALWAYS shown as the baseline —
# the API calls add/discover any extras on top of this list.
_COPILOT_DEFAULT_MODELS: list[str] = [
    # OpenAI GPT-4.1 family
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    # OpenAI GPT-4o family
    "gpt-4o",
    "gpt-4o-mini",
    # OpenAI o-series reasoning
    "o4-mini",
    "o3",
    "o3-mini",
    "o1",
    "o1-mini",
    # Anthropic Claude 4.x family
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-opus-4",
    "claude-sonnet-4",
    # Anthropic Claude 3.x family
    "claude-3.7-sonnet",
    "claude-3.5-sonnet",
    "claude-3.5-haiku",
    # Google Gemini
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

# Name fragments that signal a non-chat model — always exclude.
_NON_CHAT_NAME_FRAGMENTS = (
    "embed", "classifier", "rerank",
    "whisper", "dall-e", "tts", "speech",
)

# Task-type values that mean the model is NOT a chat-completion model.
_NON_CHAT_TASKS = {
    "embeddings", "text-embedding", "text-embeddings",
    "fill-mask", "token-classification", "text-classification",
    "image-to-text", "text-to-image", "image-generation",
    "question-answering", "summarization", "translation",
    "automatic-speech-recognition", "zero-shot-classification",
    "feature-extraction",
}


def _extract_model_name(raw_id: str) -> str:
    """Extract a clean, usable model name from a raw API model ID.

    Handles AzureML registry URIs such as:
    ``azureml://registries/azure-openai/models/gpt-4o/versions/2``  →  ``gpt-4o``
    """
    raw_id = raw_id.strip()
    if raw_id.startswith("azureml://"):
        m = _re.search(r"/models/([^/]+)/versions/", raw_id)
        if m:
            return m.group(1)
    return raw_id


def _is_chat_model(item: dict, name: str) -> bool:
    """Return True only when the model is a chat-completion model."""
    task = item.get("task") or item.get("capabilities") or ""
    if isinstance(task, list):
        task = " ".join(task)
    if any(t in task.lower() for t in _NON_CHAT_TASKS):
        return False
    name_lower = name.lower()
    if any(frag in name_lower for frag in _NON_CHAT_NAME_FRAGMENTS):
        return False
    return True


def _is_copilot_provider(item: dict, name: str) -> bool:
    """Return True for models from providers covered by GitHub Copilot."""
    _ok_publishers = {"openai", "azure-openai", "anthropic", "google", "microsoft"}
    publisher = (
        item.get("publisher") or item.get("publisher_name") or item.get("vendor") or ""
    ).lower()
    if publisher in _ok_publishers:
        return True
    raw_id = (item.get("id") or "").lower()
    if "openai" in raw_id or "anthropic" in raw_id or "google" in raw_id:
        return True
    name_lower = name.lower()
    ok_prefixes = ("gpt-", "o1", "o3", "o4", "claude-", "gemini-")
    return any(name_lower.startswith(p) for p in ok_prefixes)


def _sort_models(models: list[str]) -> list[str]:
    def _key(m: str) -> tuple[int, str]:
        ml = m.lower()
        if ml.startswith("gpt-5"):   return (0, ml)
        if ml.startswith("gpt-4"):   return (1, ml)
        if ml.startswith("o4"):      return (2, ml)
        if ml.startswith("o3"):      return (3, ml)
        if ml.startswith("o"):       return (4, ml)
        if ml.startswith("claude"):  return (5, ml)
        if ml.startswith("gemini"):  return (6, ml)
        return (9, ml)
    return sorted(models, key=_key)


def _extract_from_api_items(items: list) -> list[str]:
    """Sanitize and filter a raw API model list down to usable chat-completion model ids."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        raw_id   = item.get("id") or ""
        raw_name = item.get("name") or ""
        mid = _extract_model_name(raw_name) or _extract_model_name(raw_id)
        if not mid or mid in seen:
            continue
        if not _is_copilot_provider(item, mid):
            continue
        if not _is_chat_model(item, mid):
            continue
        seen.add(mid)
        out.append(mid)
    return out


def _merge_models(api_models: list[str], baseline: list[str]) -> list[str]:
    """Merge API-discovered models with the known baseline.

    API models that are already in the baseline keep the baseline name (preserving
    known-good casing/format).  Unknown extra models from the API are appended.
    The result is sorted by provider priority.
    """
    # Normalise for case-insensitive dedup
    baseline_lower = {m.lower(): m for m in baseline}
    merged: dict[str, str] = dict(baseline_lower)  # lower → canonical name

    for m in api_models:
        ml = m.lower()
        if ml not in merged:
            merged[ml] = m  # new model discovered via API

    return _sort_models(list(merged.values()))


@router.get("/copilot-models")
async def get_copilot_models():
    """Return chat-completion models for this GitHub Copilot account.

    Strategy:
    1. Try ``api.githubcopilot.com/models`` (Copilot-native, returns only
       subscription models for the authenticated account).
    2. On failure / empty result, try ``models.inference.ai.azure.com/models``
       (full marketplace, filtered to Copilot providers).
    3. The result is always *merged with the hard-coded baseline* so the user
       never sees an incomplete list just because an API call returned a subset.
    """
    # Always start with the full known list
    baseline = list(_COPILOT_DEFAULT_MODELS)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return {"models": baseline, "source": "fallback"}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

            # ── 1. Copilot-native API ──────────────────────────────────────
            api_models: list[str] = []
            try:
                resp = await client.get(_COPILOT_API_ENDPOINT, headers=headers)
                logger.info("Copilot API status: %d", resp.status_code)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data if isinstance(data, list) else data.get("data", data.get("models", []))
                    api_models = _extract_from_api_items(items)
                    logger.info("Copilot API returned %d usable models", len(api_models))
            except Exception as exc:
                logger.warning("Copilot API call failed: %s", exc)

            # ── 2. Marketplace fallback (adds anything not in Copilot API) ─
            if not api_models:
                try:
                    resp2 = await client.get(_GITHUB_MODELS_ENDPOINT, headers=headers)
                    logger.info("Marketplace API status: %d", resp2.status_code)
                    if resp2.status_code == 200:
                        data2 = resp2.json()
                        items2 = data2 if isinstance(data2, list) else data2.get("data", data2.get("models", []))
                        api_models = _extract_from_api_items(items2)
                        logger.info("Marketplace API returned %d usable models", len(api_models))
                except Exception as exc2:
                    logger.warning("Marketplace API call failed: %s", exc2)

            # Always merge with baseline so user sees the full known catalog
            merged = _merge_models(api_models, baseline)
            source = "api_merged" if api_models else "fallback"
            return {"models": merged, "source": source}

    except Exception as exc:
        logger.warning("Could not fetch Copilot models: %s — using defaults", exc)

    return {"models": baseline, "source": "fallback"}


@router.post("/open-terminal")
def open_in_terminal_route(body: OpenTerminalRequest):
    """Open any shell command in the user's native terminal emulator."""
    _open_in_terminal(body.command)
    return {"opened": True}


# ── Persistent MCP-setup terminal sessions ───────────────────────────────────
# Each session keeps ONE terminal window open and relays subsequent commands
# via a polling temp-file, so all MCP setup steps for the same CLI tool share
# one window instead of spawning a new window per step.

_mcp_sessions: dict[str, dict] = {}  # session_key -> {proc, cmd_file, session_dir}


def _start_mcp_session(session_key: str, first_cmd: str) -> None:
    """Spawn a new persistent PowerShell window for the given session key."""
    session_dir = Path(tempfile.mkdtemp(prefix=f"agtos_{session_key}_"))
    cmd_file = session_dir / "cmd.txt"

    # Escape backslashes for embedding inside a PowerShell single-quoted string
    cmd_file_ps = str(cmd_file).replace("\\", "\\\\")

    # Bootstrap: run the first command, then poll cmd_file for subsequent ones.
    bootstrap = (
        f"$_cmdFile = '{cmd_file_ps}'\n"
        f"# --- initial command ---\n"
        f"{first_cmd}\n"
        f"# --- poll for subsequent commands ---\n"
        f"while ($true) {{\n"
        f"    if (Test-Path $_cmdFile) {{\n"
        f"        $nextCmd = Get-Content $_cmdFile -Raw\n"
        f"        Remove-Item $_cmdFile -Force\n"
        f"        try {{ Invoke-Expression $nextCmd }}\n"
        f"        catch {{ Write-Host \"Command error: $_\" -ForegroundColor Red }}\n"
        f"    }}\n"
        f"    Start-Sleep -Milliseconds 300\n"
        f"}}\n"
    )
    encoded = _b64.b64encode(bootstrap.encode("utf-16-le")).decode("ascii")
    ps_args = ["powershell.exe", "-NoExit", "-EncodedCommand", encoded]

    if sys.platform == "win32":
        proc = (
            subprocess.Popen(["wt", "--"] + ps_args, shell=False)
            if _which("wt")
            else subprocess.Popen(ps_args, shell=False)
        )
    else:
        # macOS / Linux: use the same logic as _open_in_terminal but with bash
        shell_init = "source ~/.zshrc 2>/dev/null; source ~/.bashrc 2>/dev/null; "
        full = shell_init + first_cmd + "\n# poll not applicable on non-Windows"
        if sys.platform == "darwin":
            safe = full.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
            script = f'tell application "Terminal"\n  do script "{safe}"\n  activate\nend tell'
            proc = subprocess.Popen(["osascript", "-e", script])
        else:
            for term in ["gnome-terminal", "xterm", "konsole", "xfce4-terminal"]:
                if _which(term):
                    proc = subprocess.Popen([term, "-e", f"bash -c '{full}; exec bash'"])
                    break
            else:
                proc = subprocess.Popen(["bash", "-c", full])

    _mcp_sessions[session_key] = {"proc": proc, "cmd_file": cmd_file, "session_dir": session_dir}


def _send_to_mcp_session(session_key: str, cmd: str) -> bool:
    """Write cmd to an existing session's polling file. Returns False if session is dead."""
    session = _mcp_sessions.get(session_key)
    if not session:
        return False
    if session["proc"].poll() is not None:
        # Process exited — remove stale entry
        shutil.rmtree(str(session["session_dir"]), ignore_errors=True)
        del _mcp_sessions[session_key]
        return False
    session["cmd_file"].write_text(cmd, encoding="utf-8")
    return True


class McpTerminalRequest(BaseModel):
    session_key: str
    command: str


@router.post("/mcp-terminal")
def run_in_mcp_terminal(body: McpTerminalRequest):
    """Run a command in a persistent per-tool terminal session for ADO MCP setup.
    Reuses the existing window when alive; opens a new one otherwise."""
    if _send_to_mcp_session(body.session_key, body.command):
        return {"reused": True}
    _start_mcp_session(body.session_key, body.command)
    return {"reused": False}


@router.get("", response_model=AllToolsStatusResponse)
async def list_tools():
    """Return installation + auth status for every supported CLI tool (parallel)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=len(TOOL_REGISTRY)) as pool:
        tasks = [
            loop.run_in_executor(pool, _get_tool_status, meta)
            for meta in TOOL_REGISTRY.values()
        ]
        tools = await asyncio.gather(*tasks)
    return AllToolsStatusResponse(tools=list(tools))


@router.get("/{tool_key}", response_model=ToolStatusResponse)
def get_tool(tool_key: str):
    """Return status for a single tool."""
    meta = TOOL_REGISTRY.get(tool_key)
    if meta is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_key}")
    return _get_tool_status(meta)


@router.post("/{tool_key}/login", response_model=ToolActionResponse)
def login_tool(tool_key: str, body: ToolActionRequest, orch=Depends(get_orchestrator)):
    """
    Trigger authentication for a CLI tool.

    For API-key auth: store the key in env + config and validate.
    For OAuth/account auth: launch the CLI's native browser-based login flow.
    """
    meta = TOOL_REGISTRY.get(tool_key)
    if meta is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_key}")

    auth_method = body.auth_method or "api_key"

    # ── API key flow ──────────────────────────────────────────────────────
    if auth_method == "api_key" and body.api_key:
        if not body.api_key or body.api_key.startswith("***"):
            return ToolActionResponse(
                success=False, message="Please provide a valid API key."
            )

        # Store into env for immediate use
        if meta.env_key:
            os.environ[meta.env_key] = body.api_key

        # Persist to config
        cfg = orch.config
        at = getattr(cfg, "ai_tools", None)
        if at is not None:
            cred = getattr(at, tool_key, None)
            if cred is not None:
                cred.api_key = body.api_key
                cred.auth_method = "api_key"
                cred.enabled = True

        # Persist to .env file
        _persist_env_key(meta.env_key, body.api_key, orch)

        # Write into the CLI tool's own native credential store so new
        # terminal sessions pick up the key without re-prompting.
        try:
            _persist_cli_credentials(tool_key, body.api_key)
        except Exception as exc:
            logger.debug("Could not write CLI credentials for %s: %s", tool_key, exc)

        return ToolActionResponse(
            success=True,
            message=f"API key configured for {meta.display_name}.",
            auth_user=f"via ${meta.env_key}" if meta.env_key else "",
        )

    # ── OAuth / account / browser login flow ──────────────────────────────
    # For tools that support browser-based auth, we launch their CLI login
    # command which opens the user's default browser for the OAuth flow.
    if auth_method in ("oauth", "account", "bedrock", "vertex", "qwen-oauth", "coding-plan"):
        if not _which(meta.binary):
            checked_alts = any(_which(a) for a in meta.alt_binaries)
            if not checked_alts:
                return ToolActionResponse(
                    success=False,
                    message=(
                        f"{meta.display_name} is not installed. "
                        f"Install it first: {meta.install_cmd}"
                    ),
                )

        # Launch the login command — this opens a browser for OAuth tools
        try:
            login_cmd = list(meta.login_cmd)

            # Some CLIs need extra flags for specific auth methods
            if tool_key == "codex" and auth_method in ("account", "oauth"):
                # Codex has no `auth login` subcommand; running `codex` bare
                # triggers an interactive OAuth/account setup on first run.
                login_cmd = ["codex"]
            elif tool_key == "claude" and auth_method == "bedrock":
                login_cmd = ["claude", "auth", "login", "--provider", "bedrock"]
            elif tool_key == "gemini" and auth_method == "oauth":
                # Gemini CLI has no `auth login` subcommand — just running `gemini`
                # triggers the OAuth browser flow automatically on first use.
                login_cmd = ["gemini"]
            elif tool_key == "gemini" and auth_method == "vertex":
                login_cmd = ["gcloud", "auth", "application-default", "login"]
            elif tool_key == "copilot":
                login_cmd = ["gh", "auth", "login", "--web"]
            elif tool_key == "qwen":
                if auth_method == "coding-plan":
                    login_cmd = ["qwen", "auth", "coding-plan"]
                else:  # qwen-oauth or account
                    login_cmd = ["qwen", "auth", "qwen-oauth"]

            # Open in an interactive terminal — needed for OAuth flows that
            # require browser callbacks or interactive prompts.
            cmd_str = " ".join(shlex.quote(c) for c in login_cmd)

            # GitHub Copilot OAuth: GITHUB_TOKEN / GH_TOKEN in the environment
            # prevent gh from storing real OAuth credentials.  Unset them in the
            # terminal session so the browser flow runs and writes to gh's keychain.
            # The env vars remain intact for all other processes (git, API calls).
            if tool_key == "copilot":
                if _IS_WINDOWS:
                    cmd_str = (
                        "Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue; "
                        "Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue; "
                        + cmd_str
                    )
                else:
                    cmd_str = "unset GITHUB_TOKEN GH_TOKEN; " + cmd_str

            _open_in_terminal(cmd_str)

            # Update config to reflect the login attempt
            cfg = orch.config
            at = getattr(cfg, "ai_tools", None)
            if at is not None:
                cred = getattr(at, tool_key, None)
                if cred is not None:
                    cred.auth_method = auth_method
                    cred.enabled = True

            return ToolActionResponse(
                success=True,
                message=(
                    f"Opened Terminal for {meta.display_name} authentication. "
                    "Complete the login in the Terminal window, then refresh status."
                ),
                requires_browser=True,
            )

        except Exception as exc:
            return ToolActionResponse(
                success=False,
                message=f"Failed to launch login: {str(exc)[:300]}",
            )

    return ToolActionResponse(
        success=False,
        message=f"Unsupported auth method: {auth_method}",
    )


@router.post("/{tool_key}/logout", response_model=ToolActionResponse)
def logout_tool(tool_key: str, orch=Depends(get_orchestrator)):
    """Clear credentials for a tool — both env and CLI session."""
    meta = TOOL_REGISTRY.get(tool_key)
    if meta is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_key}")

    errors: list[str] = []

    # 1. Clear env var
    if meta.env_key and meta.env_key in os.environ:
        del os.environ[meta.env_key]

    # 2. Run CLI logout command (if installed)
    if _which(meta.binary) and meta.logout_cmd:
        rc, out, err = _run(meta.logout_cmd, timeout=10)
        if rc != 0:
            errors.append(f"CLI logout returned code {rc}: {err}")

    # Qwen has no logout subcommand — clear credential files instead
    if meta.key == "qwen":
        import shutil
        for candidate in [
            os.path.expanduser("~/.qwen"),
            os.path.expanduser("~/.config/qwen"),
            os.path.expanduser("~/.local/share/qwen"),
        ]:
            if os.path.exists(candidate):
                try:
                    shutil.rmtree(candidate)
                except Exception as exc:
                    errors.append(f"Could not clear {candidate}: {exc}")

    # Remove the key from the CLI's native credential store for all tools
    try:
        _remove_cli_credentials(tool_key)
    except Exception as exc:
        logger.debug("Could not remove CLI credentials for %s: %s", tool_key, exc)

    # 3. Clear from config
    cfg = orch.config
    at = getattr(cfg, "ai_tools", None)
    if at is not None:
        cred = getattr(at, tool_key, None)
        if cred is not None:
            cred.api_key = ""
            cred.auth_method = ""
            cred.enabled = False
            cred.email = ""

    if errors:
        return ToolActionResponse(
            success=False,
            message=f"Partial logout: {'; '.join(errors)}",
        )

    return ToolActionResponse(
        success=True,
        message=f"Logged out of {meta.display_name}.",
    )


@router.post("/{tool_key}/refresh", response_model=ToolStatusResponse)
def refresh_tool(tool_key: str):
    """Re-check installation and auth status for a single tool."""
    meta = TOOL_REGISTRY.get(tool_key)
    if meta is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_key}")
    return _get_tool_status(meta)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _persist_env_key(env_key: str | None, value: str, orch: Any) -> None:
    """Write an env-var value to the project's .env file so it survives restarts."""
    if not env_key:
        return
    try:
        from pathlib import Path
        from ..deps import orch_holder

        agent_os_root = (
            Path(orch_holder.config_path).parent
            if orch_holder.config_path
            else Path(".").resolve()
        )
        env_path = agent_os_root / ".env"
        lines: list[str] = []
        if env_path.exists():
            lines = env_path.read_text().splitlines()

        prefix = f"{env_key}="
        updated = False
        result: list[str] = []
        for ln in lines:
            if ln.startswith(prefix):
                result.append(f'{env_key}="{value}"')
                updated = True
            else:
                result.append(ln)
        if not updated:
            result.append(f'{env_key}="{value}"')

        env_path.write_text("\n".join(result) + "\n")
        logger.info("Persisted %s to %s", env_key, env_path)
    except Exception as exc:
        logger.debug("Could not persist env key %s: %s", env_key, exc)


def _write_shell_export(var_name: str, value: str) -> None:
    """Upsert 'export VAR=value  # agent_os' in ~/.zshrc and ~/.bashrc."""
    import re
    from pathlib import Path

    marker = "# agent_os"
    line = f'export {var_name}="{value}"  {marker}\n'
    pattern = re.compile(rf'^export {re.escape(var_name)}=.*{re.escape(marker)}.*$', re.MULTILINE)

    for rc in [Path.home() / ".zshrc", Path.home() / ".bashrc"]:
        try:
            text = rc.read_text() if rc.exists() else ""
            if pattern.search(text):
                text = pattern.sub(line.rstrip(), text)
            else:
                text = text.rstrip("\n") + "\n" + line
            rc.write_text(text)
            logger.info("Wrote %s to %s", var_name, rc)
        except Exception as exc:
            logger.warning("Could not write to %s: %s", rc, exc)


def _remove_shell_export(var_name: str) -> None:
    """Remove agent_os-managed export lines for var_name from shell rc files."""
    import re
    from pathlib import Path

    marker = "# agent_os"
    pattern = re.compile(rf'^export {re.escape(var_name)}=.*{re.escape(marker)}.*\n?', re.MULTILINE)

    for rc in [Path.home() / ".zshrc", Path.home() / ".bashrc"]:
        try:
            if rc.exists():
                text = rc.read_text()
                updated = pattern.sub("", text)
                if updated != text:
                    rc.write_text(updated)
                    logger.info("Removed %s from %s", var_name, rc)
        except Exception as exc:
            logger.warning("Could not update %s: %s", rc, exc)


# Map each tool to its shell env-var name
_TOOL_ENV_VAR: dict[str, str] = {
    "codex":    "OPENAI_API_KEY",
    "claude":   "ANTHROPIC_API_KEY",
    "gemini":   "GEMINI_API_KEY",
    "qwen":     "DASHSCOPE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def _persist_cli_credentials(tool_key: str, api_key: str) -> None:
    """
    Write the API key into the CLI tool's native credential store AND into the
    user's shell rc files so every new terminal picks it up without re-prompting.
    """
    import json
    from pathlib import Path

    # --- Shell rc export (works for all env-var-based tools) ---
    env_var = _TOOL_ENV_VAR.get(tool_key)
    if env_var:
        _write_shell_export(env_var, api_key)

    if tool_key == "codex":
        # Codex reads ~/.codex/auth.json — format: {"auth_mode": "apikey", "OPENAI_API_KEY": "sk-..."}
        path = Path.home() / ".codex" / "auth.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                pass
        existing["auth_mode"] = "apikey"
        existing["OPENAI_API_KEY"] = api_key
        path.write_text(json.dumps(existing, indent=2))
        logger.info("Wrote Codex credentials to %s", path)

    elif tool_key == "claude":
        # Also write to ~/.claude/credentials.json (used by some claude versions)
        path = Path.home() / ".claude" / "credentials.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                pass
        existing["api_key"] = api_key
        path.write_text(json.dumps(existing, indent=2))
        logger.info("Wrote Claude credentials to %s", path)

    elif tool_key == "gemini":
        # Also write to ~/.gemini/credentials.json
        path = Path.home() / ".gemini" / "credentials.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                pass
        existing["api_key"] = api_key
        path.write_text(json.dumps(existing, indent=2))
        logger.info("Wrote Gemini credentials to %s", path)

    elif tool_key == "qwen":
        # Also write to ~/.qwen/config.json
        path = Path.home() / ".qwen" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                pass
        existing["api_key"] = api_key
        path.write_text(json.dumps(existing, indent=2))
        logger.info("Wrote Qwen credentials to %s", path)

    elif tool_key == "deepseek":
        # Also write to ~/.deepseek/config.json
        path = Path.home() / ".deepseek" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                pass
        existing["api_key"] = api_key
        path.write_text(json.dumps(existing, indent=2))
        logger.info("Wrote DeepSeek credentials to %s", path)


def _remove_cli_credentials(tool_key: str) -> None:
    """Wipe API keys from a tool's native credential store and shell rc files on logout."""
    import json
    from pathlib import Path

    # --- Remove shell rc export ---
    env_var = _TOOL_ENV_VAR.get(tool_key)
    if env_var:
        _remove_shell_export(env_var)

    credential_files: dict[str, Path] = {
        "codex":    Path.home() / ".codex" / "auth.json",
        "claude":   Path.home() / ".claude" / "credentials.json",
        "gemini":   Path.home() / ".gemini" / "credentials.json",
        "qwen":     Path.home() / ".qwen" / "config.json",
        "deepseek": Path.home() / ".deepseek" / "config.json",
    }
    path = credential_files.get(tool_key)
    if path and path.exists():
        if tool_key == "codex":
            try:
                data = json.loads(path.read_text())
                data.pop("OPENAI_API_KEY", None)
                data.pop("auth_mode", None)
                path.write_text(json.dumps(data, indent=2))
            except Exception:
                path.unlink(missing_ok=True)
        else:
            path.unlink(missing_ok=True)
        logger.info("Removed %s credential file %s", tool_key, path)
