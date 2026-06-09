"""Environment resolver — resolves secrets from config → .env file → os.environ.

Priority order (highest wins):
1. Value in config.yaml secrets section (non-empty string)
2. Value in .env file at project root
3. Value in os.environ

Never logs actual secret values.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ENV_MAP = {
    "openai_api_key": "OPENAI_API_KEY",
    "github_token": "GITHUB_TOKEN",
    "ollama_base_url": "OLLAMA_BASE_URL",
}

# Additional env var aliases checked as fallbacks (in order) when _ENV_MAP lookup fails.
_ENV_ALIASES: dict[str, list[str]] = {
    "github_token": ["GITHUB_PAT_TOKEN", "GITHUB_TOKEN"],
}


def _load_dotenv(project_root: str = ".") -> dict[str, str]:
    """Parse a .env file into a dict. Ignores comments and blank lines."""
    env_path = Path(project_root) / ".env"
    if not env_path.is_file():
        return {}
    pairs: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            pairs[key] = value
    return pairs


def auto_load_dotenv(project_root: str = ".") -> None:
    """Load .env file from project_root into os.environ at startup.

    Values already present in os.environ are NOT overwritten, so real
    environment variables always win over the .env file.
    """
    pairs = _load_dotenv(project_root)
    for key, value in pairs.items():
        if key not in os.environ:
            os.environ[key] = value
            logger.debug("Loaded '%s' from .env into os.environ", key)


def resolve_secret(
    field: str,
    config_value: str = "",
    project_root: str = ".",
) -> str:
    """Resolve a single secret field.

    Args:
        field: The config field name (e.g. "openai_api_key").
        config_value: The value from config.yaml (may be empty).
        project_root: Path to the project root for .env lookup.

    Returns:
        The resolved value, or empty string if not found anywhere.
    """
    if config_value:
        logger.debug("Secret '%s' resolved from config.yaml", field)
        return config_value

    env_var = _ENV_MAP.get(field, field.upper())
    # Build the list of env var names to try: primary + any registered aliases.
    candidates: list[str] = _ENV_ALIASES.get(field, [env_var])
    if env_var not in candidates:
        candidates = [env_var] + candidates

    dotenv = _load_dotenv(project_root)
    for candidate in candidates:
        dotenv_val = dotenv.get(candidate, "")
        if dotenv_val:
            logger.debug("Secret '%s' resolved from .env file (%s)", field, candidate)
            return dotenv_val

    for candidate in candidates:
        os_val = os.environ.get(candidate, "")
        if os_val:
            logger.debug("Secret '%s' resolved from os.environ (%s)", field, candidate)
            return os_val

    logger.debug("Secret '%s' not found in any source", field)
    return ""


def resolve_all(
    config_openai: str = "",
    config_github: str = "",
    project_root: str = ".",
) -> dict[str, str]:
    """Resolve all known secrets at once.

    Returns:
        Dict with keys "openai_api_key" and "github_token".
    """
    return {
        "openai_api_key": resolve_secret(
            "openai_api_key", config_openai, project_root,
        ),
        "github_token": resolve_secret(
            "github_token", config_github, project_root,
        ),
    }


def mask_secret(value: str) -> str:
    """Mask a secret for safe display. Shows first 3 and last 4 chars."""
    if not value or len(value) < 10:
        return "***" if value else ""
    return f"{value[:3]}...{value[-4:]}"


def build_codex_env(
    config_openai: str = "",
    project_root: str = ".",
) -> dict[str, str]:
    """Build the environment dict for Codex subprocess invocation.

    Starts from os.environ and overrides OPENAI_API_KEY with the
    resolved value (config → .env → env).

    Also ensures PYTHONPATH contains the agent-os package root so that
    subprocesses launched from a different working directory (e.g. the
    user's project folder) can still import ``agent_os``.
    """
    import sys
    env = {**os.environ}
    resolved = resolve_secret("openai_api_key", config_openai, project_root)
    if resolved:
        env["OPENAI_API_KEY"] = resolved

    # Derive the agent-os root (two levels up from this file: config/env.py → agent_os/ → root)
    agent_os_root = str(Path(__file__).resolve().parent.parent.parent)
    existing_pythonpath = env.get("PYTHONPATH", "")
    sep = ";" if sys.platform == "win32" else ":"
    if agent_os_root not in existing_pythonpath.split(sep):
        env["PYTHONPATH"] = agent_os_root + (sep + existing_pythonpath if existing_pythonpath else "")

    return env
