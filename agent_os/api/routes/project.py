"""Project routes — browse generated files, open in VS Code."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/project", tags=["project"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class FileNode(BaseModel):
    name: str
    path: str  # relative to project root
    is_dir: bool
    children: Optional[List["FileNode"]] = None
    size: Optional[int] = None


class ProjectInfoResponse(BaseModel):
    name: str
    root_path: str
    language: str
    exists: bool
    file_count: int


class FileContentResponse(BaseModel):
    path: str
    content: str
    size: int


class OpenResponse(BaseModel):
    success: bool
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/info", response_model=ProjectInfoResponse)
def get_project_info(orch=Depends(get_orchestrator)):
    """Basic info about the target project folder."""
    cfg = orch.config
    root = cfg.project.root_path
    root_path = Path(root) if root else None
    exists = root_path.exists() if root_path else False
    file_count = 0
    if exists and root_path:
        # Count only source files, excluding venv/cache/git dirs
        for f in root_path.rglob("*"):
            if not f.is_file():
                continue
            # Skip if any part of the path is an ignored directory
            if any(part in _IGNORE for part in f.parts):
                continue
            file_count += 1
    return ProjectInfoResponse(
        name=cfg.project.name or "(unnamed)",
        root_path=root or "(not set)",
        language=cfg.project.language,
        exists=exists,
        file_count=file_count,
    )


@router.get("/files", response_model=List[FileNode])
def list_project_files(orch=Depends(get_orchestrator)):
    """Return the file tree of the generated project, or [] if not yet set up."""
    root_str = orch.config.project.root_path
    if not root_str:
        return []
    root = Path(root_str)
    if not root.exists():
        return []
    return _build_tree(root, root, max_depth=6)


@router.get("/file-content", response_model=FileContentResponse)
def get_file_content(path: str, orch=Depends(get_orchestrator)):
    """Read a single file from the generated project."""
    root = _get_project_root(orch)
    target = (root / path).resolve()

    # Prevent path traversal
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        raise HTTPException(status_code=500, detail="Cannot read file")

    return FileContentResponse(
        path=path,
        content=content,
        size=target.stat().st_size,
    )


@router.post("/open-in-vscode", response_model=OpenResponse)
def open_in_vscode(orch=Depends(get_orchestrator)):
    """Open the project folder in VS Code."""
    root = _get_project_root(orch)
    try:
        # On Windows, 'code' is 'code.cmd' — a batch file that requires shell=True
        subprocess.Popen(
            ["code", str(root)],
            shell=sys.platform == "win32",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return OpenResponse(success=True, message=f"Opened {root} in VS Code")
    except FileNotFoundError:
        return OpenResponse(success=False, message="'code' command not found. Install VS Code CLI.")
    except Exception as exc:
        return OpenResponse(success=False, message=str(exc)[:200])


@router.post("/open-in-finder", response_model=OpenResponse)
def open_in_finder(orch=Depends(get_orchestrator)):
    """Open the project folder in Finder / Explorer."""
    root = _get_project_root(orch)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return OpenResponse(success=True, message=f"Opened {root} in Explorer")
    except Exception as exc:
        return OpenResponse(success=False, message=str(exc)[:200])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IGNORE = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _get_project_root(orch) -> Path:
    root = orch.config.project.root_path
    if not root:
        raise HTTPException(status_code=404, detail="Project root_path not configured")
    p = Path(root)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Project folder does not exist: {root}")
    return p


def _build_tree(current: Path, root: Path, max_depth: int, depth: int = 0) -> list[FileNode]:
    """Recursively build a file tree, skipping ignored directories."""
    if depth > max_depth:
        return []

    nodes: list[FileNode] = []
    try:
        entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return []

    for entry in entries:
        if entry.name.startswith(".") and entry.name not in (".env",):
            continue
        if entry.name in _IGNORE:
            continue

        rel = str(entry.relative_to(root))
        if entry.is_dir():
            children = _build_tree(entry, root, max_depth, depth + 1)
            nodes.append(FileNode(name=entry.name, path=rel, is_dir=True, children=children))
        else:
            nodes.append(FileNode(name=entry.name, path=rel, is_dir=False, size=entry.stat().st_size))

    return nodes
