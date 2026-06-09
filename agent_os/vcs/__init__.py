"""VCS provider abstraction — Phase 3.5.

Exposes a unified ``VCSClient`` interface consumed by all runners.
The concrete implementation (GitHub or Azure DevOps) is selected at runtime
by ``make_vcs_client`` based on ``config.requirements.source``.

Usage::

    from agent_os.vcs import make_vcs_client, VCSClient
    vcs = make_vcs_client(config)   # returns GitHubVCSClient or ADOVCSClient
    result = vcs.create_pr("feat: add login", "feature/login", "main", body)
"""

from .base import VCSClient, VCSResult
from .github_client import GitHubVCSClient
from .ado_client import ADOVCSClient
from .factory import make_vcs_client

__all__ = [
    "VCSClient",
    "VCSResult",
    "GitHubVCSClient",
    "ADOVCSClient",
    "make_vcs_client",
]
