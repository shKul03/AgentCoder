"""Abstract VCS client interface — Phase 3.5.

All runner code should depend on ``VCSClient`` + ``VCSResult`` only.
Never import ``GitHubVCSClient`` or ``ADOVCSClient`` from runner modules;
use ``make_vcs_client(config)`` instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VCSResult:
    """Uniform result type returned by every VCSClient method.

    Mirrors ``GitHubResult`` so runner code can use ``.success``, ``.data``,
    ``.error``, ``.status_code`` without knowing the underlying provider.
    """

    success: bool = True
    status_code: int = 0
    data: dict[str, Any] | None = None
    error: str = ""


class VCSClient(ABC):
    """Unified interface for git / PR operations.

    Concrete implementations: ``GitHubVCSClient``, ``ADOVCSClient``.
    All methods are synchronous (matching the rest of the Agent OS pipeline).

    The ``repo_name`` parameter is accepted by a few methods for providers
    that need it at call time (ADO repos require the repository GUID).
    For GitHub-backed instances the repo was already bound at construction.
    """

    # ── Repository ──────────────────────────────────────────────────────────

    @abstractmethod
    def get_remote_url(self, repo_name: str) -> str:
        """Return an authenticated git remote URL suitable for ``git push``.

        GitHub:  ``https://x-access-token:{token}@github.com/{owner}/{repo}.git``
        ADO:     ``https://{token}@dev.azure.com/{org}/{project}/_git/{repo}``
        """

    @abstractmethod
    def create_repo(self, repo_name: str) -> VCSResult:
        """Create a new remote repository and return its metadata in ``data``.

        ``data`` must include ``"clone_url"`` (str) at minimum.
        """

    # ── Pull Requests ────────────────────────────────────────────────────────

    @abstractmethod
    def create_pr(
        self,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> VCSResult:
        """Open a pull request from *head* to *base*.

        Returns ``data`` with at least ``"number"`` (int) and ``"html_url"`` (str).
        """

    @abstractmethod
    def get_pr(self, pr_id: int) -> VCSResult:
        """Fetch PR metadata.

        Returns ``data`` with at least ``"head"`` (dict with key ``"sha"``),
        ``"html_url"`` (str), and ``"title"`` (str).
        """

    @abstractmethod
    def get_pr_diff(self, pr_id: int) -> VCSResult:
        """Fetch the unified diff text for a PR.

        Returns ``data["diff"]`` as a raw diff string.
        """

    @abstractmethod
    def get_pr_head_sha(self, pr_id: int) -> str:
        """Return the current head commit SHA of the PR's source branch."""

    @abstractmethod
    def merge_pr(self, pr_number: int, commit_message: str = "") -> VCSResult:
        """Merge (complete) the pull request."""

    def find_open_pr(self, head_branch: str) -> int | None:
        """Return the PR number for an open PR from *head_branch*, or ``None``.

        Default implementation returns ``None``; providers override as needed.
        """
        return None

    # ── Comments ─────────────────────────────────────────────────────────────

    @abstractmethod
    def add_pr_review_comment(
        self,
        pr_number: int,
        body: str,
        commit_id: str,
        path: str,
        line: int,
    ) -> VCSResult:
        """Post an inline review comment on a specific file line.

        GitHub: ``POST /repos/.../pulls/{pr}/comments``
        ADO:    ``POST .../pullrequests/{pr}/threads`` with ``threadContext``
        """

    @abstractmethod
    def add_pr_comment(self, pr_number: int, body: str) -> VCSResult:
        """Post a global (non-inline) PR comment.

        GitHub: ``POST /repos/.../issues/{pr}/comments``
        ADO:    ``POST .../pullrequests/{pr}/threads`` (no threadContext)
        """

    @abstractmethod
    def resolve_all_pr_review_comments(self, pr_number: int) -> list[VCSResult]:
        """Resolve (mark-fixed) all open review comments / threads on the PR."""

    def get_pr_files(self, pr_id: int) -> VCSResult:
        """Fetch the list of files changed in a PR (includes per-file patches).

        Returns a list of file dicts in ``data`` (or ``data["files"]`` for ADO).
        Each dict should have at minimum ``"filename"`` and ``"patch"`` keys.

        Default implementation returns a failure result; providers override as
        needed.  The code reviewer falls back to this when the unified diff is
        empty.
        """
        return VCSResult(success=False, error="get_pr_files not implemented for this provider")

    # ── Branches ─────────────────────────────────────────────────────────────

    @abstractmethod
    def delete_branch(self, branch: str) -> VCSResult:
        """Delete a remote branch by name."""
