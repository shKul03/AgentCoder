"""RepoCloner — shallow-clones a GitHub repo and extracts read-only context.

Phase 5: GitHub Repository Input Mode (Backend).

Security notes:
- Only HTTPS ``https://github.com/`` URLs are accepted; credentials embedded
  in the URL (``https://token@github.com/...``) are explicitly rejected.
- ``git clone`` is invoked with a list argument vector (never shell=True) so
  there is no shell-injection surface.
- The clone directory is a ``tempfile.TemporaryDirectory``; the caller is
  responsible for cleanup (the returned ``ClonerResult`` exposes the path).
"""

from __future__ import annotations

import fnmatch
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.schema import GitHubInputConfig

logger = logging.getLogger(__name__)

# Maximum chars per file included in context (prevents single huge files from
# consuming the entire token budget).
_MAX_FILE_CHARS = 20_000

# Regex that matches valid GitHub HTTPS URLs without embedded credentials.
_GITHUB_HTTPS_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(\.git)?/?$"
)


def _glob_match(rel_path: str, pattern: str) -> bool:
    """Match *rel_path* against a glob pattern that may contain ``**``.

    Python 3.9's ``fnmatch.fnmatch`` doesn't understand ``**`` as a
    cross-directory wildcard.  This helper deals with that by:

    1. Trying a direct ``fnmatch`` match.
    2. For ``**/``-prefixed patterns: stripping the ``**/`` and trying the
       simplified pattern against every suffix of the path (split by ``/``).
    3. For patterns with no directory component: matching just the basename.
    """
    normed = rel_path.replace("\\", "/")

    # 1. Direct fnmatch (covers exact filenames like "README.md")
    if fnmatch.fnmatch(normed, pattern):
        return True

    if "**" in pattern:
        # 2. Strip leading **/ and try against each path suffix
        # e.g. "**/node_modules/**" → "node_modules/**"
        #      "**/*.py"            → "*.py"
        simplified = pattern
        while simplified.startswith("**/"):
            simplified = simplified[3:]
        parts = normed.split("/")
        for i in range(len(parts)):
            candidate = "/".join(parts[i:])
            if fnmatch.fnmatch(candidate, simplified):
                return True

    # 3. Basename-only match for simple patterns (e.g. "*.py", "README.md")
    if "/" not in pattern:
        basename = normed.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(basename, pattern):
            return True

    return False


@dataclass
class FileEntry:
    """A single file extracted from the cloned repo."""

    path: str  # relative to repo root
    content: str  # UTF-8 text (truncated if > _MAX_FILE_CHARS)
    truncated: bool = False


@dataclass
class ClonerResult:
    """Output of a ``RepoCloner.clone()`` call."""

    source_url: str
    clone_dir: str  # absolute path to the temp directory
    file_tree: list[str] = field(default_factory=list)  # all matched relative paths
    files: list[FileEntry] = field(default_factory=list)
    total_matched: int = 0  # matched before cap
    capped: bool = False  # True when max_context_files was hit

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation suitable for pipeline metadata."""
        return {
            "source_url": self.source_url,
            "file_tree": self.file_tree,
            "total_matched": self.total_matched,
            "capped": self.capped,
            "files": [
                {
                    "path": f.path,
                    "content": f.content,
                    "truncated": f.truncated,
                }
                for f in self.files
            ],
        }


class RepoCloner:
    """Shallow-clone a GitHub repository and return structured file context."""

    def __init__(self, config: "GitHubInputConfig") -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_url(self, url: str) -> None:
        """Raise ``ValueError`` if *url* is not a safe GitHub HTTPS URL.

        Rejects:
        - non-HTTPS schemes (``git://``, ``ssh://``, etc.)
        - embedded credentials (``https://token@github.com/...``)
        - non-github.com hosts
        """
        if not url:
            raise ValueError("source_repo_url must not be empty.")
        if not _GITHUB_HTTPS_RE.match(url):
            raise ValueError(
                f"Invalid source_repo_url: {url!r}. "
                "Only public HTTPS GitHub URLs are accepted "
                "(e.g. https://github.com/owner/repo)."
            )

    def clone(self) -> ClonerResult:
        """Clone the configured repo and return a ``ClonerResult``.

        The clone is written to a fresh ``tempfile.TemporaryDirectory``.
        The directory is **not** automatically cleaned up — callers should
        either use the path and delete it, or ignore it (OS will GC on exit).
        """
        url = self._config.source_repo_url
        self.validate_url(url)

        tmp_dir = tempfile.mkdtemp(prefix="agent_os_repo_")
        clone_target = Path(tmp_dir) / "repo"

        depth = self._config.clone_depth
        logger.info("Cloning %s (depth=%d) → %s", url, depth, clone_target)

        cmd = [
            "git", "clone",
            "--depth", str(depth),
            "--filter=blob:none",  # partial clone — speeds up large repos
            "--no-tags",
            url,
            str(clone_target),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git clone failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        matched = self._collect_files(clone_target)
        total_matched = len(matched)
        capped = total_matched > self._config.max_context_files
        selected = matched[: self._config.max_context_files]

        file_tree = [str(p.relative_to(clone_target)) for p in matched]
        files: list[FileEntry] = []
        for p in selected:
            entry = self._read_file(p, clone_target)
            if entry:
                files.append(entry)

        return ClonerResult(
            source_url=url,
            clone_dir=tmp_dir,
            file_tree=file_tree,
            files=files,
            total_matched=total_matched,
            capped=capped,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_files(self, root: Path) -> list[Path]:
        """Return all files under *root* matching include/exclude patterns."""
        include_patterns = self._config.include_file_patterns
        exclude_patterns = self._config.exclude_patterns

        all_files: list[Path] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root))
            if self._is_excluded(rel, exclude_patterns):
                continue
            if self._matches_any(rel, include_patterns):
                all_files.append(path)

        return all_files

    @staticmethod
    def _is_excluded(rel_path: str, patterns: list[str]) -> bool:
        return any(_glob_match(rel_path, pat) for pat in patterns)

    @staticmethod
    def _matches_any(rel_path: str, patterns: list[str]) -> bool:
        return any(_glob_match(rel_path, pat) for pat in patterns)

    @staticmethod
    def _read_file(path: Path, root: Path) -> FileEntry | None:
        """Read a file as UTF-8 text; skip binary or unreadable files."""
        rel = str(path.relative_to(root))
        try:
            raw = path.read_bytes()
            # Quick binary check — if the first 8 KB contains a null byte,
            # treat the file as binary and skip it.
            if b"\x00" in raw[:8192]:
                return None
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping %s: %s", rel, exc)
            return None

        truncated = len(text) > _MAX_FILE_CHARS
        if truncated:
            text = text[:_MAX_FILE_CHARS]
        return FileEntry(path=rel, content=text, truncated=truncated)
