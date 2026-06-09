"""Git operations manager — branch management, commits, and tagging.

Wraps Git CLI subprocess calls. All operations are scoped to the
project working directory from config. Operations gracefully degrade
when Git is disabled in config or the working directory is not a repo.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GitResult:
    """Result of a Git operation."""
    success: bool
    command: str
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0


class GitOpsManager:
    """Manages Git operations for the Agent OS pipeline.

    All methods are idempotent where possible — creating an existing branch
    returns success, committing with no changes is a no-op, etc.
    """

    def __init__(self, working_dir: str, remote: str = "origin") -> None:
        self._cwd = Path(working_dir)
        self._remote = remote

    # ── Low-level ──────────────────────────────────────────────────

    def _run(self, *args: str) -> GitResult:
        """Run a git command and return a GitResult."""
        cmd = ["git"] + list(args)
        cmd_str = " ".join(cmd)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._cwd),
                capture_output=True,
                text=True,
                timeout=60,
            )
            result = GitResult(
                success=proc.returncode == 0,
                command=cmd_str,
                stdout=proc.stdout.strip(),
                stderr=proc.stderr.strip(),
                return_code=proc.returncode,
            )
            if not result.success:
                logger.debug("Git command exited %d: %s — %s", proc.returncode, cmd_str, result.stderr)
            return result
        except FileNotFoundError:
            return GitResult(success=False, command=cmd_str, stderr="git not found on PATH")
        except subprocess.TimeoutExpired:
            return GitResult(success=False, command=cmd_str, stderr="git command timed out")

    # ── Repository checks ─────────────────────────────────────────

    def is_repo(self) -> bool:
        """Check if the working directory is inside a Git repository."""
        return self._run("rev-parse", "--is-inside-work-tree").success

    def current_branch(self) -> Optional[str]:
        """Return the current branch name, or None if not in a repo."""
        result = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout if result.success else None

    # ── Branch operations ─────────────────────────────────────────

    def branch_exists(self, branch: str) -> bool:
        """Check if a local branch exists."""
        result = self._run("rev-parse", "--verify", f"refs/heads/{branch}")
        return result.success

    def create_branch(self, branch: str, base: str = "HEAD") -> GitResult:
        """Create a new branch from the given base. No-op if it exists."""
        if self.branch_exists(branch):
            logger.debug("Branch %s already exists", branch)
            return GitResult(success=True, command=f"git branch {branch}", stdout="already exists")
        return self._run("branch", branch, base)

    def checkout(self, branch: str) -> GitResult:
        """Switch to the given branch."""
        return self._run("checkout", branch)

    def create_and_checkout(self, branch: str, base: str = "HEAD") -> GitResult:
        """Create a branch and switch to it. If it exists, just checkout."""
        if self.branch_exists(branch):
            return self.checkout(branch)
        return self._run("checkout", "-b", branch, base)

    # ── Staging & committing ──────────────────────────────────────

    def stage_all(self) -> GitResult:
        """Stage all changes (git add -A)."""
        return self._run("add", "-A")

    def has_staged_changes(self) -> bool:
        """Check if there are staged changes."""
        result = self._run("diff", "--cached", "--quiet")
        return not result.success  # exit 1 means there are differences

    def has_changes(self) -> bool:
        """Check if there are any uncommitted changes (staged or unstaged)."""
        result = self._run("status", "--porcelain")
        return bool(result.stdout)

    def commit(self, message: str) -> GitResult:
        """Commit staged changes. No-op if nothing is staged."""
        if not self.has_staged_changes():
            self.stage_all()
            if not self.has_staged_changes():
                return GitResult(
                    success=True, command="git commit",
                    stdout="nothing to commit",
                )
        return self._run("commit", "-m", message)

    def commit_all(self, message: str) -> GitResult:
        """Stage all and commit. No-op if working tree is clean."""
        self.stage_all()
        if not self.has_staged_changes():
            return GitResult(
                success=True, command="git commit",
                stdout="nothing to commit",
            )
        return self._run("commit", "-m", message)

    # ── Tags ──────────────────────────────────────────────────────

    def tag(self, tag_name: str, message: str = "") -> GitResult:
        """Create an annotated tag. No-op if it exists."""
        check = self._run("rev-parse", f"refs/tags/{tag_name}")
        if check.success:
            return GitResult(success=True, command=f"git tag {tag_name}", stdout="already exists")
        if message:
            return self._run("tag", "-a", tag_name, "-m", message)
        return self._run("tag", tag_name)

    def list_tags(self, prefix: str = "") -> list[str]:
        """Return tags matching the given prefix, sorted by version."""
        result = self._run("tag", "-l", f"{prefix}*", "--sort=version:refname")
        if not result.success or not result.stdout:
            return []
        return result.stdout.splitlines()

    # ── Push operations ───────────────────────────────────────────

    def push(self, branch: str, force: bool = False) -> GitResult:
        """Push a local branch to the remote."""
        args = ["push", self._remote, branch]
        if force:
            args = ["push", "--force", self._remote, branch]
        return self._run(*args)

    def push_tags(self) -> GitResult:
        """Push all tags to the remote."""
        return self._run("push", self._remote, "--tags")

    # ── Reset operations ──────────────────────────────────────────

    def reset_hard(self, ref: str) -> GitResult:
        """Hard-reset working tree and index to the given ref."""
        return self._run("reset", "--hard", ref)

    def has_merge_conflict(self) -> bool:
        """Check if there are unresolved merge conflicts."""
        result = self._run("diff", "--name-only", "--diff-filter=U")
        return bool(result.stdout)

    # ── Log info ──────────────────────────────────────────────────

    def latest_commit_sha(self) -> Optional[str]:
        """Return the short SHA of the latest commit."""
        result = self._run("rev-parse", "--short", "HEAD")
        return result.stdout if result.success else None

    def log_oneline(self, n: int = 5) -> list[str]:
        """Return the last n commits as one-line strings."""
        result = self._run("log", f"--oneline", f"-{n}")
        if not result.success:
            return []
        return result.stdout.splitlines()

    # ── Clone operations ──────────────────────────────────────────

    @staticmethod
    def clone(url: str, dest: str, depth: int = 0) -> "GitResult":
        """Clone a remote repository to a local destination.

        Args:
            url: Remote URL (token should be embedded for auth).
            dest: Absolute or relative local path for the cloned repo.
            depth: If > 0, create a shallow clone with that commit depth.

        Returns:
            GitResult indicating success/failure and any subprocess output.
        """
        cmd = ["git", "clone"]
        if depth > 0:
            cmd += ["--depth", str(depth)]
        cmd += [url, dest]
        cmd_str = " ".join(c if "x-access-token" not in c else "<redacted>" for c in cmd)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # generous for large repos
            )
            return GitResult(
                success=proc.returncode == 0,
                command=cmd_str,
                stdout=proc.stdout.strip(),
                stderr=proc.stderr.strip(),
                return_code=proc.returncode,
            )
        except FileNotFoundError:
            return GitResult(success=False, command=cmd_str, stderr="git not found on PATH")
        except subprocess.TimeoutExpired:
            return GitResult(success=False, command=cmd_str, stderr="git clone timed out")

    # ── Repository initialisation helpers ────────────────────────

    def init_repo(self) -> GitResult:
        """Initialise a new git repository in ``self.project_root``.

        Idempotent — running on an already-initialised repo is a no-op.
        """
        return self._run("init")

    def set_user(self, name: str, email: str) -> None:
        """Set the local git user name and email (repo-scoped config).

        Required before committing in a freshly cloned / inited workspace.
        """
        self._run("config", "user.name", name)
        self._run("config", "user.email", email)

    def remote_exists(self, name: str = "origin") -> bool:
        """Return True if a remote with ``name`` is configured locally."""
        result = self._run("remote", "get-url", name)
        return result.success

    def add_remote(self, name: str, url: str) -> GitResult:
        """Add a remote, or update its URL if already present.

        Args:
            name: Remote alias, typically ``"origin"``.
            url:  Remote URL.  Embed the PAT for authenticated push, e.g.
                  ``https://x-access-token:{token}@github.com/owner/repo.git``.
        """
        if self.remote_exists(name):
            logger.info("Remote '%s' exists — updating URL", name)
            return self._run("remote", "set-url", name, url)
        logger.info("Adding remote '%s' → %s", name, url if "token" not in url else "<redacted>")
        return self._run("remote", "add", name, url)

    def push_upstream(self, branch: str, remote: str = "origin") -> GitResult:
        """Push ``branch`` and set it to track the remote branch.

        Equivalent to ``git push -u {remote} {branch}``.
        """
        logger.info("Pushing branch '%s' to '%s' with upstream tracking", branch, remote)
        return self._run("push", "-u", remote, branch)

    def fetch(self, remote: str = "origin", ref: str = "") -> GitResult:
        """Fetch from *remote*, optionally limited to *ref* (branch / tag)."""
        args = ["fetch", remote]
        if ref:
            args.append(ref)
        return self._run(*args)

    # ── Named clone helpers ───────────────────────────────────────────────────

    @classmethod
    def clone_and_open(
        cls,
        url: str,
        target_dir: str | Path,
        depth: int = 0,
    ) -> tuple["GitResult", Optional["GitOpsManager"]]:
        """Clone *url* into *target_dir* and return ``(result, manager)``.

        On success the returned :class:`GitOpsManager` is bound to
        *target_dir* so callers can immediately call branch / commit methods.
        On failure *manager* is ``None``.

        Args:
            url:        Authenticated remote URL.
            target_dir: Local destination path.
            depth:      Shallow-clone depth (0 = full history).
        """
        result = cls.clone(url, str(target_dir), depth=depth)
        manager: Optional["GitOpsManager"] = cls(str(target_dir)) if result.success else None
        return result, manager
