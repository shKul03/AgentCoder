"""GitHub VCS client — Phase 3.5.

Thin adapter that wraps ``agent_os.github.client.GitHubClient`` and maps its
``GitHubResult`` return values to the provider-agnostic ``VCSResult`` type.

Construction::

    client = GitHubVCSClient(token="ghp_...", owner="acme", repo="my-service")

All runner code should obtain this via ``make_vcs_client(config)`` rather than
instantiating it directly.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import VCSClient, VCSResult

logger = logging.getLogger(__name__)


def _wrap(gh_result: Any) -> VCSResult:
    """Convert a ``GitHubResult`` to a ``VCSResult``."""
    return VCSResult(
        success=gh_result.success,
        status_code=getattr(gh_result, "status_code", 0),
        data=gh_result.data or {},
        error=gh_result.error or "",
    )


class GitHubVCSClient(VCSClient):
    """``VCSClient`` backed by the GitHub REST API.

    Delegates every operation to the existing rich ``GitHubClient``; this class
    exists purely to satisfy the ``VCSClient`` interface so runners never branch
    on provider type.
    """

    def __init__(self, token: str, owner: str, repo: str) -> None:
        """
        Args:
            token: GitHub Personal Access Token with ``repo`` scope.
            owner: GitHub owner (user or org).
            repo:  Repository name (without ``owner/`` prefix).
        """
        from ..github.client import GitHubClient

        self._gh = GitHubClient(token=token, owner=owner, repo=repo)
        self._token = token
        self._owner = owner
        self._repo = repo

    def for_repo(self, repo_name: str) -> "GitHubVCSClient":
        """Return a new client targeting *repo_name* (same owner and token).

        Use this when the project repo is derived at runtime (e.g. from the
        requirements content) and differs from ``config.github.repo``.
        """
        return GitHubVCSClient(token=self._token, owner=self._owner, repo=repo_name)

    # ── Repository ──────────────────────────────────────────────────────────

    def get_remote_url(self, repo_name: str) -> str:
        """Return an authenticated HTTPS remote URL for git push.

        Embeds the PAT so ``git push`` works without interactive auth.
        """
        return f"https://x-access-token:{self._token}@github.com/{self._owner}/{repo_name}.git"

    def create_repo(self, repo_name: str) -> VCSResult:
        return _wrap(self._gh.create_repo(name=repo_name))

    # ── Pull Requests ────────────────────────────────────────────────────────

    def create_pr(
        self,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> VCSResult:
        return _wrap(self._gh.create_pr(title=title, head=head, base=base, body=body))

    def get_pr(self, pr_id: int) -> VCSResult:
        return _wrap(self._gh.get_pr(pr_id))

    def get_pr_diff(self, pr_id: int) -> VCSResult:
        return _wrap(self._gh.get_pr_diff(pr_id))

    def get_pr_head_sha(self, pr_id: int) -> str:
        return self._gh.get_pr_head_sha(pr_id) or ""

    def merge_pr(self, pr_number: int, commit_message: str = "") -> VCSResult:
        return _wrap(self._gh.merge_pr(pr_number, commit_message=commit_message))

    def find_open_pr(self, head_branch: str) -> int | None:
        """Return the PR number of an open PR from *head_branch*, or ``None``."""
        result = self._gh.list_prs(head=head_branch, state="open")
        if result.success and isinstance(result.data, list) and result.data:
            return result.data[0].get("number")
        return None

    # ── Comments ─────────────────────────────────────────────────────────────

    def add_pr_review_comment(
        self,
        pr_number: int,
        body: str,
        commit_id: str,
        path: str,
        line: int,
    ) -> VCSResult:
        return _wrap(
            self._gh.add_pr_review_comment(
                pr_number=pr_number,
                body=body,
                commit_id=commit_id,
                path=path,
                line=line,
            )
        )

    def add_pr_comment(self, pr_number: int, body: str) -> VCSResult:
        return _wrap(self._gh.add_pr_comment(pr_number=pr_number, body=body))

    def resolve_all_pr_review_comments(self, pr_number: int) -> list[VCSResult]:
        return [_wrap(r) for r in self._gh.resolve_all_pr_review_comments(pr_number)]

    def get_pr_files(self, pr_id: int) -> VCSResult:
        """Fetch the list of changed files in a PR (with per-file patch text)."""
        return _wrap(self._gh.get_pr_files(pr_id))

    # ── Branches ─────────────────────────────────────────────────────────────

    def delete_branch(self, branch: str) -> VCSResult:
        return _wrap(self._gh.delete_branch(branch))

    # ── Fork operations ──────────────────────────────────────────────────────

    def fork_repo(self, source_owner: str, source_repo: str, *, name: str = "") -> VCSResult:
        """Fork *source_owner*/*source_repo* to the authenticated user's account.

        GitHub forks are asynchronous (202 Accepted). Use :meth:`wait_for_fork`
        to confirm the fork is accessible before attempting a clone.
        """
        return _wrap(self._gh.fork_repo(source_owner, source_repo, name=name))

    def wait_for_fork(
        self,
        fork_owner: str,
        fork_repo: str,
        max_wait_seconds: int = 30,
    ) -> bool:
        """Poll until the fork exists and is accessible.

        Args:
            fork_owner: GitHub user/org that owns the fork.
            fork_repo:  Name of the forked repository.
            max_wait_seconds: Give up after this many seconds.

        Returns:
            True if fork is accessible within the timeout, False otherwise.
        """
        import time
        from ..github.client import GitHubClient

        checker = GitHubClient(token=self._token, owner=fork_owner, repo=fork_repo)
        for _ in range(max_wait_seconds):
            if checker.repo_exists():
                return True
            time.sleep(1)
        return False
