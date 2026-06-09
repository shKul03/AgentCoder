"""Configuration loading from YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import AgentOSConfig


def load_config(config_path: str | Path) -> AgentOSConfig:
    """Load and validate configuration from a YAML file.

    After loading the YAML, secrets that are empty in the file are resolved
    from the .env file and os.environ so that ``config.secrets.openai_api_key``
    and ``config.secrets.github_token`` are always populated where available —
    without storing real values in config.yaml.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    config = AgentOSConfig(**raw)

    # Resolve secrets from .env / os.environ if not present in the YAML.
    # The .env file lives next to config.yaml (i.e. the Agent OS project root).
    from .env import resolve_secret
    project_root = str(config_path.parent)

    if not config.secrets.openai_api_key:
        resolved = resolve_secret("openai_api_key", "", project_root)
        if resolved:
            config.secrets.openai_api_key = resolved

    if not config.secrets.github_token:
        resolved = resolve_secret("github_token", "", project_root)
        if resolved:
            config.secrets.github_token = resolved

    return config


def get_default_config() -> AgentOSConfig:
    """Return config with all defaults."""
    return AgentOSConfig()
