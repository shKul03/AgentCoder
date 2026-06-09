"""Azure DevOps VCS client — Phase 3.5.

Implements ``VCSClient`` against the Azure DevOps REST APIs.

Auth: ``Authorization: Basic {base64(":" + ado_pat)}`` — note the colon prefix.

ADO API version used: ``7.1``

Construction::

    client = ADOVCSClient(org="myorg", project="MyProject", token="pat_...")

Credentials come from ``config.requirements`` (ado_org, ado_project, ado_token)
which are the **same** credentials used for requirements ingestion — no extra
credential fields are required.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from .base import VCSClient, VCSResult

logger = logging.getLogger(__name__)

_API_VERSION = "7.1"
_TIMEOUT = 30.0
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.0


class ADOVCSClient(VCSClient):
    """``VCSClient`` backed by the Azure DevOps REST API.

    Key ADO concepts that differ from GitHub:
    - PRs use a *thread* model for comments (both inline and global).
    - Inline comments carry a ``threadContext`` with ``filePath`` + line range.
    - Resolving a comment means patching the thread to ``status: "fixed"``.
    - Deleting a branch requires the current tip SHA as ``oldObjectId``.
    - PR merge is ``PATCH /pullrequests/{id}`` with ``status: "completed"``.
    """

    def __init__(self, org: str, project: str, token: str) -> None:
        """
        Args:
            org:     ADO organization name (slug in dev.azure.com URL).
            project: ADO project name.
            token:   Personal Access Token with Code (Read & Write) +
                     Pull Request Contribute scopes.
        """
        if not all([org, project, token]):
            raise ValueError("ADO org, project, and token are all required")
        self._org = org
        self._project = project
        self._token = token
        # Base URL for Git REST APIs
        self._base = f"https://dev.azure.com/{org}/{project}/_apis/git"
        # Cached repo ID (fetched lazily on first use)
        self._repo_id: Optional[str] = None

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _auth_header(self) -> str:
        """Return the Basic auth header value for ADO PAT auth."""
        raw = f":{self._token}"
        encoded = base64.b64encode(raw.encode()).decode()
        return f"Basic {encoded}"

    def _headers(self, *, accept: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
            "Accept": accept,
        }

    def _request(
        self,
        method: str,
        url: str,
        json_body: Any = None,
        *,
        params: dict[str, str] | None = None,
        accept: str = "application/json",
    ) -> VCSResult:
        """Execute an HTTP request with retry on transient errors."""
        params = {**(params or {}), "api-version": _API_VERSION}

        for attempt in range(_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=_TIMEOUT) as client:
                    response = client.request(
                        method,
                        url,
                        headers=self._headers(accept=accept),
                        json=json_body,
                        params=params,
                    )

                if response.status_code in (400, 401, 403, 404, 409, 422):
                    data = {}
                    try:
                        data = response.json()
                    except Exception:
                        pass
                    msg = data.get("message") or data.get("errorCode") or response.text[:300]
                    return VCSResult(
                        success=False,
                        status_code=response.status_code,
                        error=f"ADO {response.status_code}: {msg}",
                    )

                if response.status_code in (200, 201, 204):
                    data = {}
                    if response.content and response.status_code != 204:
                        try:
                            data = response.json()
                        except Exception:
                            data = {"raw": response.text}
                    return VCSResult(success=True, status_code=response.status_code, data=data)

                # 5xx — retry
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                    continue

                return VCSResult(
                    success=False,
                    status_code=response.status_code,
                    error=f"ADO HTTP {response.status_code}: {response.text[:200]}",
                )

            except httpx.TimeoutException as exc:
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                    continue
                return VCSResult(success=False, error=f"ADO request timed out: {exc}")
            except Exception as exc:
                return VCSResult(success=False, error=f"ADO request failed: {exc}")

        return VCSResult(success=False, error="ADO request exhausted retries")

    # ── Repo ID lookup ────────────────────────────────────────────────────────

    def _get_repo_id(self, repo_name: str) -> Optional[str]:
        """Return the ADO repository GUID for *repo_name*, caching the result."""
        if self._repo_id:
            return self._repo_id
        url = f"{self._base}/repositories/{repo_name}"
        result = self._request("GET", url)
        if result.success and result.data:
            self._repo_id = result.data.get("id")
            return self._repo_id
        logger.warning("Could not resolve ADO repo ID for '%s': %s", repo_name, result.error)
        return None

    def _repo_url(self, repo_name: str) -> str:
        return f"{self._base}/repositories/{repo_name}"

    # ── Repository ────────────────────────────────────────────────────────────

    def get_remote_url(self, repo_name: str) -> str:
        """Return an authenticated HTTPS remote for git push.

        Embeds the PAT as the password component so ``git push`` works
        non-interactively.  The username is ``pat`` (ADO ignores the value).
        """
        return (
            f"https://pat:{self._token}@dev.azure.com"
            f"/{self._org}/{self._project}/_git/{repo_name}"
        )

    def create_repo(self, repo_name: str) -> VCSResult:
        """Create a new ADO Git repository.

        ``POST dev.azure.com/{org}/{project}/_apis/git/repositories``
        """
        url = f"https://dev.azure.com/{self._org}/{self._project}/_apis/git/repositories"
        body = {"name": repo_name}
        result = self._request("POST", url, json_body=body)
        if result.success and result.data:
            # Normalise to have a "clone_url" key matching the GitHub convention
            result.data.setdefault(
                "clone_url",
                result.data.get("remoteUrl", self.get_remote_url(repo_name)),
            )
            # Cache the new repo ID
            self._repo_id = result.data.get("id")
        return result

    # ── Pull Requests ─────────────────────────────────────────────────────────

    def create_pr(
        self,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> VCSResult:
        """Create an ADO pull request.

        ``POST .../git/repositories/{repo}/pullrequests``
        """
        repo_name = self._resolve_repo_name()
        url = f"{self._repo_url(repo_name)}/pullrequests"
        payload = {
            "title": title,
            "description": body,
            "sourceRefName": _ref(head),
            "targetRefName": _ref(base),
        }
        result = self._request("POST", url, json_body=payload)
        if result.success and result.data:
            # Normalise to GitHub-like shape so runner code works unchanged
            pr_id = result.data.get("pullRequestId")
            result.data["number"] = pr_id
            result.data["html_url"] = (
                f"https://dev.azure.com/{self._org}/{self._project}"
                f"/_git/{repo_name}/pullrequest/{pr_id}"
            )
        return result

    def get_pr(self, pr_id: int) -> VCSResult:
        """Fetch ADO PR metadata; normalise to GitHub-like shape."""
        repo_name = self._resolve_repo_name()
        url = f"{self._repo_url(repo_name)}/pullrequests/{pr_id}"
        result = self._request("GET", url)
        if result.success and result.data:
            # Synthesise a GitHub-compatible "head.sha" from lastMergeSourceCommit
            last_src = result.data.get("lastMergeSourceCommit") or {}
            head_sha = last_src.get("commitId", "")
            result.data.setdefault("head", {"sha": head_sha})
            result.data.setdefault(
                "html_url",
                (
                    f"https://dev.azure.com/{self._org}/{self._project}"
                    f"/_git/{repo_name}/pullrequest/{pr_id}"
                ),
            )
        return result

    def get_pr_diff(self, pr_id: int) -> VCSResult:
        """Fetch a unified diff for the PR by building it from iteration changes.

        ADO does not expose a raw unified-diff endpoint; this method:
        1. Lists changed files from the latest PR iteration.
        2. For each changed file, fetches the before/after content and builds
           a simplified diff entry.

        The resulting ``data["diff"]`` is suitable for the code reviewer
        LLM prompt.  It is not a byte-perfect git unified diff.
        """
        repo_name = self._resolve_repo_name()

        # Step 1 — get the latest iteration ID
        iter_url = f"{self._repo_url(repo_name)}/pullrequests/{pr_id}/iterations"
        iter_result = self._request("GET", iter_url)
        if not iter_result.success or not iter_result.data:
            return VCSResult(
                success=False,
                error=f"Could not fetch PR iterations: {iter_result.error}",
            )
        iterations = iter_result.data.get("value") or []
        if not iterations:
            return VCSResult(success=False, error="No iterations found for PR")
        latest_iter_id = iterations[-1].get("id", 1)

        # Step 2 — list changed files in the latest iteration
        changes_url = (
            f"{self._repo_url(repo_name)}/pullrequests/{pr_id}"
            f"/iterations/{latest_iter_id}/changes"
        )
        changes_result = self._request("GET", changes_url)
        if not changes_result.success or not changes_result.data:
            return VCSResult(
                success=False,
                error=f"Could not fetch PR changes: {changes_result.error}",
            )

        change_entries = changes_result.data.get("changeEntries") or []
        diff_parts: list[str] = []

        for entry in change_entries[:50]:  # cap at 50 files to avoid huge prompts
            item = entry.get("item") or {}
            file_path = item.get("path", "")
            change_type = entry.get("changeType", "")
            if not file_path:
                continue

            # Fetch file content from source branch
            content = self._fetch_file_content(repo_name, file_path, is_base=False)
            diff_parts.append(
                f"diff --ado a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                f"# changeType: {change_type}\n"
                + (
                    "\n".join(f"+{line}" for line in content.splitlines())
                    if content
                    else "(binary or empty file)"
                )
            )

        combined = "\n\n".join(diff_parts) or "(no changed files)"
        return VCSResult(success=True, data={"diff": combined})

    def _fetch_file_content(
        self, repo_name: str, path: str, *, is_base: bool = False
    ) -> str:
        """Return the text content of *path* from the repo (best-effort)."""
        url = (
            f"https://dev.azure.com/{self._org}/{self._project}"
            f"/_apis/git/repositories/{repo_name}/items"
        )
        result = self._request(
            "GET",
            url,
            params={
                "path": path,
                "includeContent": "true",
                "api-version": _API_VERSION,
            },
            accept="text/plain",
        )
        if result.success and result.data:
            raw = result.data.get("raw") or result.data.get("content") or ""
            return raw
        return ""

    def get_pr_head_sha(self, pr_id: int) -> str:
        result = self.get_pr(pr_id)
        if result.success and result.data:
            return (result.data.get("head") or {}).get("sha", "")
        return ""

    def merge_pr(self, pr_number: int, commit_message: str = "") -> VCSResult:
        """Complete (merge) the ADO pull request.

        ``PATCH .../pullrequests/{id}`` with ``status: "completed"``
        requires the ``lastMergeSourceCommit`` object.
        """
        repo_name = self._resolve_repo_name()

        # Fetch the current head commit to supply lastMergeSourceCommit
        pr_result = self.get_pr(pr_number)
        commit_id = ""
        if pr_result.success and pr_result.data:
            commit_id = (pr_result.data.get("head") or {}).get("sha", "")
        if not commit_id:
            return VCSResult(
                success=False,
                error="Cannot merge: could not determine lastMergeSourceCommit",
            )

        url = f"{self._repo_url(repo_name)}/pullrequests/{pr_number}"
        payload: dict[str, Any] = {
            "status": "completed",
            "lastMergeSourceCommit": {"commitId": commit_id},
        }
        if commit_message:
            payload["completionOptions"] = {"mergeCommitMessage": commit_message}

        return self._request("PATCH", url, json_body=payload)

    # ── Comments ─────────────────────────────────────────────────────────────

    def add_pr_review_comment(
        self,
        pr_number: int,
        body: str,
        commit_id: str,
        path: str,
        line: int,
    ) -> VCSResult:
        """Post an inline thread comment with file + line context.

        ADO uses a unified threads model; inline context is expressed via
        ``threadContext`` with ``filePath`` and ``rightFileStart/End``.
        Returns ``data["id"]`` = thread ID.
        """
        repo_name = self._resolve_repo_name()
        url = f"{self._repo_url(repo_name)}/pullrequests/{pr_number}/threads"
        payload = {
            "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
            "status": "active",
            "threadContext": {
                "filePath": path,
                "rightFileStart": {"line": line, "offset": 1},
                "rightFileEnd": {"line": line, "offset": 1},
            },
        }
        return self._request("POST", url, json_body=payload)

    def add_pr_comment(self, pr_number: int, body: str) -> VCSResult:
        """Post a global PR comment (no file context).

        ADO: ``POST .../pullrequests/{id}/threads`` without ``threadContext``.
        """
        repo_name = self._resolve_repo_name()
        url = f"{self._repo_url(repo_name)}/pullrequests/{pr_number}/threads"
        payload = {
            "comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
            "status": "active",
        }
        return self._request("POST", url, json_body=payload)

    def resolve_all_pr_review_comments(self, pr_number: int) -> list[VCSResult]:
        """Set all active PR threads to ``status: "fixed"``.

        ADO thread statuses: ``active`` / ``fixed`` / ``wontFix`` /
        ``closed`` / ``byDesign`` / ``pending``.
        """
        repo_name = self._resolve_repo_name()
        threads_url = f"{self._repo_url(repo_name)}/pullrequests/{pr_number}/threads"
        threads_result = self._request("GET", threads_url)

        if not threads_result.success or not threads_result.data:
            return [VCSResult(
                success=False,
                error=f"Could not fetch threads: {threads_result.error}",
            )]

        threads = threads_result.data.get("value") or []
        results: list[VCSResult] = []

        for thread in threads:
            thread_id = thread.get("id")
            status = thread.get("status", "unknown")
            if status != "active" or not thread_id:
                continue
            patch_url = f"{threads_url}/{thread_id}"
            r = self._request(
                "PATCH",
                patch_url,
                json_body={"status": "fixed"},
            )
            results.append(r)

        return results or [VCSResult(success=True, data={}, error="")]

    # ── Branches ─────────────────────────────────────────────────────────────

    def delete_branch(self, branch: str) -> VCSResult:
        """Delete a remote branch via the ADO Git Refs API.

        Requires the current tip commit SHA (``oldObjectId``).  A branch
        is deleted by sending ``newObjectId`` of all zeros.
        """
        repo_name = self._resolve_repo_name()

        # Resolve current tip SHA
        ref_name = f"heads/{branch}"
        refs_url = f"{self._repo_url(repo_name)}/refs"
        refs_result = self._request(
            "GET",
            refs_url,
            params={"filter": ref_name, "api-version": _API_VERSION},
        )
        old_sha = ""
        if refs_result.success and refs_result.data:
            for ref in (refs_result.data.get("value") or []):
                if ref.get("name") == f"refs/{ref_name}":
                    old_sha = ref.get("objectId", "")
                    break

        if not old_sha:
            return VCSResult(
                success=False,
                error=f"Could not resolve tip SHA for branch '{branch}'",
            )

        body = [
            {
                "name": f"refs/{ref_name}",
                "newObjectId": "0" * 40,
                "oldObjectId": old_sha,
            }
        ]
        return self._request("POST", refs_url, json_body=body)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_repo_name(self) -> str:
        """Return the repository name from the cached ID or fall back to project name."""
        # For non-ID operations ADO accepts the repo name directly.
        # We store it in _repo_name when available; otherwise use the project name.
        return getattr(self, "_repo_name", self._project)

    def set_repo_name(self, repo_name: str) -> None:
        """Store the ADO repository name for use in URL construction."""
        self._repo_name = repo_name
        self._repo_id = None  # invalidate cached ID


# ── Utilities ─────────────────────────────────────────────────────────────────

def _ref(branch: str) -> str:
    """Normalise a branch name to a full ADO ref (``refs/heads/…``)."""
    if branch.startswith("refs/"):
        return branch
    return f"refs/heads/{branch}"
