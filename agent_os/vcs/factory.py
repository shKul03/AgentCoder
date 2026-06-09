"""VCS client factory — Phase 3.5.

Single entry point for instantiating the correct ``VCSClient`` implementation
based on the configured requirements source.

Usage (orchestrator init)::

    from agent_os.vcs import make_vcs_client
    vcs_client = make_vcs_client(config)   # may return None if credentials are absent
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..config.schema import AgentOSConfig

from .base import VCSClient

logger = logging.getLogger(__name__)


def make_vcs_client(config: "AgentOSConfig") -> Optional[VCSClient]:
    """Instantiate the correct ``VCSClient`` from *config*.

    Selection logic:
    - ``config.vcs.provider == "ado"`` → ``ADOVCSClient``
      (credentials come from ``config.requirements.ado_*``)
    - Anything else → ``GitHubVCSClient``
      (credentials come from ``config.secrets.github_token`` +
       ``config.github.owner`` / ``config.github.repo``)

    Falls back to ``config.requirements.source`` when ``config.vcs`` is absent
    (backward compatibility with configs created before VCS decoupling).

    Returns ``None`` when the required credentials are absent so callers can
    decide whether to skip VCS operations rather than raising at runtime.
    """
    # Primary: explicit vcs.provider field
    vcs_cfg = getattr(config, "vcs", None)
    if vcs_cfg is not None:
        provider = getattr(vcs_cfg, "provider", "github")
    else:
        # Migration fallback: infer from requirements source
        provider = "ado" if getattr(config.requirements, "source", "device") == "ado" else "github"

    if provider == "ado":
        return _make_ado_client(config)
    return _make_github_client(config)


def _make_github_client(config: "AgentOSConfig") -> Optional[VCSClient]:
    from .github_client import GitHubVCSClient

    token = getattr(config.secrets, "github_token", "") or ""
    owner = getattr(config.github, "owner", "") or ""
    repo = getattr(config.github, "repo", "") or ""

    if not all([token, owner, repo]):
        logger.warning(
            "GitHub VCS client: incomplete credentials "
            "(need github_token, github.owner, github.repo)"
        )
        return None

    logger.debug("VCS provider: GitHub (owner=%s, repo=%s)", owner, repo)
    return GitHubVCSClient(token=token, owner=owner, repo=repo)


def _make_ado_client(config: "AgentOSConfig") -> Optional[VCSClient]:
    from .ado_client import ADOVCSClient

    org = getattr(config.requirements, "ado_org", "") or ""
    project = getattr(config.requirements, "ado_project", "") or ""
    token = getattr(config.requirements, "ado_token", "") or ""

    if not all([org, project, token]):
        logger.warning(
            "ADO VCS client: incomplete credentials "
            "(need requirements.ado_org, ado_project, ado_token)"
        )
        return None

    repo_name = getattr(config.project, "repo_name", "") or getattr(config.github, "repo", "") or ""

    logger.debug("VCS provider: Azure DevOps (org=%s, project=%s)", org, project)
    client = ADOVCSClient(org=org, project=project, token=token)
    if repo_name:
        client.set_repo_name(repo_name)
    return client
