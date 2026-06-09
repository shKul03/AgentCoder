"""GitHub REST API client — thin wrapper for push, PR, and comment operations.

Uses httpx for HTTP calls. All methods are synchronous (matching the rest of
the Agent OS pipeline). Requires a resolved GitHub token.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_TIMEOUT = 30.0
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.0


@dataclass
class GitHubResult:
    """Result of a GitHub API operation."""
    success: bool
    status_code: int = 0
    data: dict[str, Any] | None = None
    error: str = ""


class GitHubClient:
    """Minimal GitHub REST API client for Agent OS pipeline operations.

    Supports: create PR, add PR comment, merge PR, get PR.
    Push is handled by GitOpsManager (local git push subprocess).
    """

    def __init__(self, token: str, owner: str, repo: str) -> None:
        if not token:
            raise ValueError("GitHub token is required")
        if not owner or not repo:
            raise ValueError("GitHub owner and repo are required")
        self._token = token
        self._owner = owner
        self._repo = repo
        self._base_url = f"{_GITHUB_API}/repos/{owner}/{repo}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        *,
        absolute_url: str = "",
    ) -> GitHubResult:
        """Make an HTTP request with retry logic."""
        url = absolute_url or f"{self._base_url}{path}"

        for attempt in range(_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=_TIMEOUT) as client:
                    response = client.request(
                        method, url, headers=self._headers(), json=json_body,
                    )

                if response.status_code == 422:
                    # Validation error — don't retry
                    data = response.json() if response.content else {}
                    msg = data.get("message", response.text[:200])
                    api_errors = data.get("errors", [])
                    if api_errors:
                        details = "; ".join(
                            e.get("message", str(e)) if isinstance(e, dict) else str(e)
                            for e in api_errors
                        )
                        msg = f"{msg} — {details}"
                    logger.warning(
                        "GitHub 422 Validation Failed on %s %s [owner: %s, repo: %s]: %s",
                        method, path, self._owner, self._repo, msg,
                    )
                    return GitHubResult(
                        success=False, status_code=422,
                        data=data, error=msg,
                    )

                if response.status_code >= 500 and attempt < _MAX_RETRIES:
                    wait = _BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        "GitHub API %d on %s %s — retrying in %.1fs",
                        response.status_code, method, path, wait,
                    )
                    time.sleep(wait)
                    continue

                if response.status_code >= 400:
                    data = response.json() if response.content else {}
                    msg = data.get("message", response.text[:200])
                    return GitHubResult(
                        success=False, status_code=response.status_code,
                        data=data, error=msg,
                    )

                data = response.json() if response.content else {}
                return GitHubResult(
                    success=True, status_code=response.status_code, data=data,
                )

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                    continue
                return GitHubResult(success=False, error="Request timed out")
            except httpx.HTTPError as exc:
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                    continue
                return GitHubResult(success=False, error=str(exc))

        return GitHubResult(success=False, error="Max retries exceeded")

    # ── PR operations ─────────────────────────────────────────────

    def create_pr(
        self,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> GitHubResult:
        """Create a pull request.

        Args:
            title: PR title.
            head: Head branch (the feature branch).
            base: Base branch to merge into.
            body: PR description body.

        Returns:
            GitHubResult with data containing PR number in data["number"].
        """
        logger.info(
            "Creating PR: %s (%s → %s) [actor: %s, repo: %s/%s]",
            title, head, base, self._owner, self._owner, self._repo,
        )
        return self._request("POST", "/pulls", {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
        })

    def get_pr(self, pr_number: int) -> GitHubResult:
        """Get details of a pull request."""
        return self._request("GET", f"/pulls/{pr_number}")

    def list_prs(self, head: str = "", state: str = "open") -> GitHubResult:
        """List pull requests, optionally filtered by head branch and state.

        ``head`` should be ``owner:branch`` for cross-fork or just ``branch``
        (``owner:`` is prepended automatically when no colon is present).
        """
        params: dict[str, str] = {"state": state, "per_page": "5"}
        if head:
            if ":" not in head:
                head = f"{self._owner}:{head}"
            params["head"] = head
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        logger.debug(
            "Listing PRs (head=%s, state=%s) [actor: %s, repo: %s/%s]",
            head, state, self._owner, self._owner, self._repo,
        )
        return self._request("GET", f"/pulls?{qs}")

    def merge_pr(
        self,
        pr_number: int,
        merge_method: str = "squash",
        commit_message: str = "",
    ) -> GitHubResult:
        """Merge a pull request.

        Args:
            pr_number: PR number to merge.
            merge_method: One of "merge", "squash", "rebase".
            commit_message: Optional custom commit message.
        """
        logger.info("Merging PR #%d via %s", pr_number, merge_method)
        body: dict[str, Any] = {"merge_method": merge_method}
        if commit_message:
            body["commit_message"] = commit_message
        return self._request("PUT", f"/pulls/{pr_number}/merge", body)

    def add_pr_comment(self, pr_number: int, body: str) -> GitHubResult:
        """Add a comment on a PR (uses the issues API)."""
        logger.info("Adding comment to PR #%d", pr_number)
        return self._request("POST", f"/issues/{pr_number}/comments", {
            "body": body,
        })

    def close_pr(self, pr_number: int) -> GitHubResult:
        """Close a pull request without merging."""
        return self._request("PATCH", f"/pulls/{pr_number}", {
            "state": "closed",
        })

    # ── Repository operations ─────────────────────────────────────

    def create_repo(self, name: str, description: str = "", private: bool = False) -> GitHubResult:
        """Create a new GitHub repository under the authenticated user's account.

        Args:
            name: Repository name.
            description: Short description.
            private: Whether the repo is private.

        Returns:
            GitHubResult with data containing clone_url, html_url, full_name.
        """
        logger.info("Creating GitHub repo: %s [actor: %s]", name, self._owner)
        return self._request(
            "POST", "", json_body={
                "name": name,
                "description": description,
                "private": private,
                "auto_init": False,
            },
            absolute_url=f"{_GITHUB_API}/user/repos",
        )

    def repo_exists(self) -> bool:
        """Check if the configured owner/repo exists on GitHub."""
        result = self._request("GET", "")
        return result.success

    def fork_repo(self, owner: str, repo: str, *, name: str = "") -> GitHubResult:
        """Fork a GitHub repository to the authenticated user's account.

        Args:
            owner: Owner of the source repository (org or user).
            repo: Name of the source repository.
            name: Optional name for the fork. If omitted GitHub uses the
                  source repository name.

        Returns:
            GitHubResult with data containing the fork's full name, clone URL,
            and HTML URL.  A 202 response indicates the fork is being created
            asynchronously — it is still treated as success.
        """
        logger.info("Forking %s/%s%s", owner, repo, f" as {name}" if name else "")
        body: dict = {"name": name} if name else {}
        result = self._request(
            "POST",
            "",
            json_body=body,
            absolute_url=f"{_GITHUB_API}/repos/{owner}/{repo}/forks",
        )
        # GitHub returns 202 Accepted for fork operations (async) — treat as success
        if result.status_code == 202 or result.success:
            result.success = True
        return result

    def get_authenticated_user(self) -> GitHubResult:
        """Return the currently authenticated user's login and metadata."""
        return self._request(
            "GET", "", absolute_url=f"{_GITHUB_API}/user"
        )

    # ── PR review comment operations ──────────────────────────────

    def add_pr_review_comment(
        self,
        pr_number: int,
        body: str,
        commit_id: str,
        path: str,
        line: int,
        side: str = "RIGHT",
    ) -> GitHubResult:
        """Add an inline review comment to a specific file/line in a PR.

        Args:
            pr_number: Pull request number.
            body: Comment text (markdown supported).
            commit_id: The SHA of the commit to comment on (latest = PR head).
            path: File path relative to the repo root.
            line: Line number in the diff to attach the comment to.
            side: "RIGHT" (post-change) or "LEFT" (pre-change).
        """
        logger.info("Adding inline review comment to PR #%d — %s:%d", pr_number, path, line)
        return self._request("POST", f"/pulls/{pr_number}/comments", {
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": side,
        })

    def get_pr_review_comments(self, pr_number: int) -> GitHubResult:
        """Get all review (inline) comments on a PR.

        Returns data as a list of comment dicts, each containing at minimum:
          ``id``, ``body``, ``path``, ``node_id``.
        """
        return self._request("GET", f"/pulls/{pr_number}/comments")

    def reply_to_review_comment(
        self, pr_number: int, comment_id: int, body: str
    ) -> GitHubResult:
        """Reply to an existing inline review comment thread.

        This is the REST equivalent of "resolving" a conversation — in the GitHub
        UI, adding a reply is the standard way to mark a thread as addressed when
        the GraphQL ``resolveReviewThread`` mutation is not available.
        """
        logger.info("Replying to review comment %d on PR #%d", comment_id, pr_number)
        return self._request(
            "POST", f"/pulls/{pr_number}/comments/{comment_id}/replies", {"body": body}
        )

    def resolve_review_thread(self, thread_node_id: str) -> GitHubResult:
        """Resolve a pull request review thread via the GraphQL API.

        Falls back gracefully if the PAT lacks the ``pull_requests:write`` scope.
        """
        logger.info("Resolving review thread %s via GraphQL", thread_node_id)
        mutation = """
mutation ResolveThread($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""
        api_key = self._token
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(
                    "https://api.github.com/graphql",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"query": mutation, "variables": {"threadId": thread_node_id}},
                )
            if resp.status_code == 200:
                data = resp.json()
                if "errors" not in data:
                    return GitHubResult(success=True, status_code=200, data=data)
                err_msg = "; ".join(e.get("message", "") for e in data["errors"])
                return GitHubResult(success=False, status_code=200, error=err_msg)
            return GitHubResult(
                success=False, status_code=resp.status_code, error=resp.text[:200]
            )
        except Exception as exc:
            return GitHubResult(success=False, error=str(exc))

    def resolve_all_pr_review_comments(self, pr_number: int) -> list[GitHubResult]:
        """Reply "Implemented ✅" to every open review comment thread on a PR.

        Used by the code generator to mark all reviewer comments as addressed.
        Returns one result per comment.
        """
        results: list[GitHubResult] = []
        comments_result = self.get_pr_review_comments(pr_number)
        if not comments_result.success or not comments_result.data:
            return [comments_result]

        comments = comments_result.data if isinstance(comments_result.data, list) else []
        for comment in comments:
            comment_id = comment.get("id")
            if not comment_id:
                continue
            # Try GraphQL thread resolution first (requires node_id)
            node_id = comment.get("node_id", "")
            if node_id:
                r = self.resolve_review_thread(node_id)
                if r.success:
                    results.append(r)
                    continue
            # Fallback: add a "Fixed ✅" reply
            r = self.reply_to_review_comment(pr_number, comment_id, "Implemented ✅")
            results.append(r)

        logger.info(
            "Resolved %d/%d review comments on PR #%d",
            sum(1 for r in results if r.success), len(results), pr_number,
        )
        return results

    # ── Branch operations ─────────────────────────────────────────

    def delete_branch(self, branch: str) -> GitHubResult:
        """Delete a remote branch by name (e.g. ``dev``, ``feature/agent-os``).

        Treats a 422 "Reference does not exist" response as success — this
        happens when GitHub auto-deleted the branch after PR merge.
        """
        logger.info("Deleting remote branch: %s", branch)
        result = self._request("DELETE", f"/git/refs/heads/{branch}")
        if not result.success and result.status_code == 422:
            msg = result.error or ""
            if "does not exist" in msg.lower() or "reference" in msg.lower():
                logger.info(
                    "Branch '%s' already deleted (422 Reference does not exist) — treating as success",
                    branch,
                )
                return GitHubResult(success=True, status_code=200, data={}, error="")
        return result

    def get_pr_head_sha(self, pr_number: int) -> Optional[str]:
        """Return the head commit SHA of a PR, or None on failure."""
        result = self.get_pr(pr_number)
        if result.success and result.data:
            return (result.data.get("head") or {}).get("sha")
        return None

    # ── PR diff ───────────────────────────────────────────────────

    def get_pr_diff(self, pr_number: int) -> GitHubResult:
        """Fetch the unified diff of a pull request.

        Uses the ``application/vnd.github.diff`` media type to get a plain
        unified diff string rather than JSON.
        """
        logger.info("Fetching diff for PR #%d", pr_number)
        url = f"{self._base_url}/pulls/{pr_number}"
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.get(
                    url,
                    headers={
                        **self._headers(),
                        "Accept": "application/vnd.github.diff",
                    },
                )
            if resp.status_code >= 400:
                return GitHubResult(
                    success=False, status_code=resp.status_code, error=resp.text[:200]
                )
            return GitHubResult(
                success=True,
                status_code=resp.status_code,
                data={"diff": resp.text},
            )
        except Exception as exc:
            return GitHubResult(success=False, error=str(exc))

    def get_pr_files(self, pr_number: int) -> GitHubResult:
        """Fetch the list of files changed in a PR (includes patch/diff per file)."""
        return self._request("GET", f"/pulls/{pr_number}/files")

