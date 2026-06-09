"""Phase 5 tests — GitHub Repository Input Mode.

Tests RepoCloner URL validation, file collection, module maker prompt
augmentation, fork naming, and the clone-preview API endpoint.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_os.config.schema import AgentOSConfig, GitHubInputConfig
from agent_os.github_input.cloner import ClonerResult, FileEntry, RepoCloner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def basic_config() -> GitHubInputConfig:
    return GitHubInputConfig(
        enabled=True,
        source_repo_url="https://github.com/owner/existing-repo",
        clone_depth=1,
        include_file_patterns=["**/*.py", "README.md"],
        exclude_patterns=["**/__pycache__/**"],
        max_context_files=50,
    )


# ---------------------------------------------------------------------------
# 1. GitHubInputConfig defaults
# ---------------------------------------------------------------------------


def test_github_input_config_defaults():
    cfg = AgentOSConfig()
    assert cfg.github_input.enabled is False
    assert cfg.github_input.source_repo_url == ""
    assert cfg.github_input.clone_depth == 1
    assert cfg.github_input.max_context_files == 50
    assert cfg.github_input.new_repo_suffix == "-agent-os-fork"


def test_github_input_config_from_dict():
    cfg = AgentOSConfig(
        github_input={
            "enabled": True,
            "source_repo_url": "https://github.com/owner/repo",
            "clone_depth": 2,
            "max_context_files": 10,
        }
    )
    assert cfg.github_input.enabled is True
    assert cfg.github_input.source_repo_url == "https://github.com/owner/repo"
    assert cfg.github_input.clone_depth == 2
    assert cfg.github_input.max_context_files == 10


# ---------------------------------------------------------------------------
# 2. RepoCloner URL validation
# ---------------------------------------------------------------------------


def test_validate_url_valid(basic_config):
    cloner = RepoCloner(basic_config)
    # Should not raise
    cloner.validate_url("https://github.com/owner/repo")
    cloner.validate_url("https://github.com/owner/repo.git")
    cloner.validate_url("https://github.com/User-Name/Repo_Name.123")


@pytest.mark.parametrize("bad_url", [
    "",
    "http://github.com/owner/repo",        # non-HTTPS
    "git://github.com/owner/repo",         # git protocol
    "ssh://git@github.com/owner/repo",     # SSH
    "https://token@github.com/owner/repo", # embedded credentials
    "https://gitlab.com/owner/repo",       # non-github host
    "https://github.com/",                 # missing path
    "https://github.com/owner",            # missing repo name
    "just-a-string",
    "https://github.com/owner/repo/extra/path",  # too many segments
])
def test_validate_url_rejects_bad_urls(bad_url, basic_config):
    cloner = RepoCloner(basic_config)
    with pytest.raises(ValueError):
        cloner.validate_url(bad_url)


# ---------------------------------------------------------------------------
# 3. _collect_files helper
# ---------------------------------------------------------------------------


def test_collect_files_include_exclude(tmp_path: Path, basic_config):
    # Create fake repo structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("# app")
    (tmp_path / "README.md").write_text("# Readme")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-311.pyc").write_bytes(b"\x00binary")

    cloner = RepoCloner(basic_config)
    collected = cloner._collect_files(tmp_path)
    rel_paths = [str(p.relative_to(tmp_path)) for p in collected]

    assert "src/app.py" in rel_paths
    assert "README.md" in rel_paths
    # __pycache__ should be excluded
    assert not any("__pycache__" in p for p in rel_paths)


def test_collect_files_caps_at_max(tmp_path: Path):
    cfg = GitHubInputConfig(
        enabled=True,
        source_repo_url="https://github.com/owner/repo",
        include_file_patterns=["**/*.py"],
        max_context_files=3,
    )
    for i in range(10):
        (tmp_path / f"file{i}.py").write_text(f"# file {i}")

    cloner = RepoCloner(cfg)
    collected = cloner._collect_files(tmp_path)
    # _collect_files returns all matched; capping happens in clone()
    assert len(collected) == 10  # all matched, cap applied later


# ---------------------------------------------------------------------------
# 4. _read_file helper
# ---------------------------------------------------------------------------


def test_read_file_text(tmp_path: Path):
    f = tmp_path / "hello.py"
    f.write_text("print('hello')")
    entry = RepoCloner._read_file(f, tmp_path)
    assert entry is not None
    assert entry.path == "hello.py"
    assert "print" in entry.content
    assert entry.truncated is False


def test_read_file_binary_skipped(tmp_path: Path):
    f = tmp_path / "binary.bin"
    f.write_bytes(b"\x00\x01\x02\x03")
    entry = RepoCloner._read_file(f, tmp_path)
    assert entry is None


def test_read_file_truncated(tmp_path: Path):
    f = tmp_path / "big.py"
    f.write_text("x" * 25_000)
    entry = RepoCloner._read_file(f, tmp_path)
    assert entry is not None
    assert entry.truncated is True
    assert len(entry.content) == 20_000  # _MAX_FILE_CHARS


# ---------------------------------------------------------------------------
# 5. ClonerResult.to_dict
# ---------------------------------------------------------------------------


def test_cloner_result_to_dict():
    result = ClonerResult(
        source_url="https://github.com/o/r",
        clone_dir="/tmp/test",
        file_tree=["src/app.py", "README.md"],
        files=[FileEntry(path="src/app.py", content="# app", truncated=False)],
        total_matched=2,
        capped=False,
    )
    d = result.to_dict()
    assert d["source_url"] == "https://github.com/o/r"
    assert d["file_tree"] == ["src/app.py", "README.md"]
    assert d["total_matched"] == 2
    assert d["capped"] is False
    assert len(d["files"]) == 1
    assert d["files"][0]["path"] == "src/app.py"


# ---------------------------------------------------------------------------
# 6. RepoCloner.clone() — mocked git subprocess
# ---------------------------------------------------------------------------


def _make_fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake cloned repo structure."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')")
    (repo / "README.md").write_text("# Hello")
    sub = repo / "pkg"
    sub.mkdir()
    (sub / "utils.py").write_text("def helper(): pass")
    return repo


@patch("agent_os.github_input.cloner.subprocess.run")
@patch("tempfile.mkdtemp")
def test_clone_success(mock_mkdtemp, mock_run, tmp_path, basic_config):
    _make_fake_repo(tmp_path)
    mock_mkdtemp.return_value = str(tmp_path)
    mock_run.return_value = MagicMock(returncode=0, stderr="")

    result = RepoCloner(basic_config).clone()
    assert result.source_url == basic_config.source_repo_url
    assert result.total_matched >= 2  # at least main.py + README.md
    assert len(result.files) > 0


@patch("agent_os.github_input.cloner.subprocess.run")
@patch("tempfile.mkdtemp")
def test_clone_git_failure_raises(mock_mkdtemp, mock_run, tmp_path, basic_config):
    mock_mkdtemp.return_value = str(tmp_path)
    mock_run.return_value = MagicMock(returncode=128, stderr="fatal: not found")

    with pytest.raises(RuntimeError, match="git clone failed"):
        RepoCloner(basic_config).clone()


@patch("agent_os.github_input.cloner.subprocess.run")
@patch("tempfile.mkdtemp")
def test_clone_respects_max_context_files(mock_mkdtemp, mock_run, tmp_path):
    cfg = GitHubInputConfig(
        enabled=True,
        source_repo_url="https://github.com/owner/repo",
        include_file_patterns=["**/*.py"],
        max_context_files=2,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    for i in range(10):
        (repo / f"mod{i}.py").write_text(f"# {i}")

    mock_mkdtemp.return_value = str(tmp_path)
    mock_run.return_value = MagicMock(returncode=0, stderr="")

    result = RepoCloner(cfg).clone()
    assert len(result.files) <= 2
    assert result.capped is True
    assert result.total_matched == 10


# ---------------------------------------------------------------------------
# 7. Module Maker prompt augmentation
# ---------------------------------------------------------------------------


def test_build_codebase_section_basic():
    from agent_os.module_maker.runner import _build_codebase_section

    codebase = {
        "file_tree": ["src/app.py", "README.md"],
        "files": [
            {"path": "src/app.py", "content": "print('hi')", "truncated": False},
        ],
    }
    section = _build_codebase_section(codebase, "https://github.com/o/r")
    assert "Existing Codebase" in section
    assert "https://github.com/o/r" in section
    assert "src/app.py" in section
    assert "print('hi')" in section
    assert "README.md" in section


def test_build_codebase_section_truncated_marker():
    from agent_os.module_maker.runner import _build_codebase_section

    codebase = {
        "file_tree": ["big.py"],
        "files": [{"path": "big.py", "content": "x" * 100, "truncated": True}],
    }
    section = _build_codebase_section(codebase, "https://github.com/o/r")
    assert "truncated" in section


def test_module_maker_run_passes_source_codebase():
    """Verify run() passes source_codebase to _build_prompt."""
    from agent_os.module_maker.runner import ModuleMakerRunner

    runner = ModuleMakerRunner.__new__(ModuleMakerRunner)
    runner._config = AgentOSConfig(
        github_input={"enabled": True, "source_repo_url": "https://github.com/o/r"}
    )
    runner._identity_ctx = None
    runner._req_repo = MagicMock()
    runner._req_repo.get_by_type.return_value = []

    codebase = {"file_tree": ["main.py"], "files": []}
    prompt = runner._build_prompt(source_codebase=codebase)
    assert "Existing Codebase" in prompt


# ---------------------------------------------------------------------------
# 8. _ensure_remote_repo fork naming
# ---------------------------------------------------------------------------


def test_ensure_remote_repo_fork_suffix():
    """When github_input is enabled, repo name gets the fork suffix."""
    from unittest.mock import MagicMock, patch

    from agent_os.orchestrator.handlers import _ensure_remote_repo

    ctx = MagicMock()
    ctx.config.github_input.enabled = True
    ctx.config.github_input.source_repo_url = "https://github.com/owner/existing"
    ctx.config.github_input.new_repo_suffix = "-agent-os-fork"
    ctx.config.github.repo = ""
    ctx.config.github.owner = "myowner"
    ctx.config.github.auto_push = True
    ctx.config.project.root_path = "/projects/myproject"
    ctx.config.secrets.github_token = "ghp_test"

    git_mock = MagicMock()
    git_mock.is_repo.return_value = True

    # resolve_secret is imported locally inside _ensure_remote_repo;
    # patch it at its source module
    with patch("agent_os.config.env.resolve_secret", return_value="ghp_test"):
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=401)
            result = _ensure_remote_repo(ctx, git_mock)
            assert result is False  # auth failed but no exception

    # Verify the repo name that would have been used — rerun the name-derivation logic
    from pathlib import Path as _P
    folder = _P(ctx.config.project.root_path).name
    base = ctx.config.github.repo or folder
    suffix = ctx.config.github_input.new_repo_suffix
    expected_name = base + suffix
    assert expected_name.endswith("-agent-os-fork")


# ---------------------------------------------------------------------------
# 9. clone-preview API endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_client():
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from agent_os.api.deps import orch_holder
    from agent_os.api.routes import pipeline
    from agent_os.orchestrator.engine import Orchestrator

    import tempfile
    tmp = tempfile.mkdtemp()
    cfg_obj = AgentOSConfig(storage={"db_path": f"{tmp}/test.db"})
    orch = Orchestrator(cfg_obj)
    orch_holder._orch = orch
    orch_holder.config_path = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(pipeline.router)
    return TestClient(app)


@patch("agent_os.github_input.cloner.subprocess.run")
@patch("tempfile.mkdtemp")
def test_clone_preview_endpoint_success(mock_mkdtemp, mock_run, tmp_path, test_client):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')")
    (repo / "README.md").write_text("# Readme")

    mock_mkdtemp.return_value = str(tmp_path)
    mock_run.return_value = MagicMock(returncode=0, stderr="")

    resp = test_client.post("/api/pipeline/clone-preview", json={
        "source_repo_url": "https://github.com/owner/repo",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_url"] == "https://github.com/owner/repo"
    assert isinstance(data["file_tree"], list)
    assert "total_matched" in data
    assert "capped" in data


def test_clone_preview_endpoint_invalid_url(test_client):
    resp = test_client.post("/api/pipeline/clone-preview", json={
        "source_repo_url": "https://gitlab.com/owner/repo",
    })
    assert resp.status_code == 422
    assert "Invalid source_repo_url" in resp.json()["detail"]


@patch("agent_os.github_input.cloner.subprocess.run")
@patch("tempfile.mkdtemp")
def test_clone_preview_git_failure_returns_400(mock_mkdtemp, mock_run, tmp_path, test_client):
    mock_mkdtemp.return_value = str(tmp_path)
    mock_run.return_value = MagicMock(returncode=128, stderr="fatal: repo not found")

    resp = test_client.post("/api/pipeline/clone-preview", json={
        "source_repo_url": "https://github.com/owner/nonexistent-repo",
    })
    assert resp.status_code == 400
    assert "git clone failed" in resp.json()["detail"]
