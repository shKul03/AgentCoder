"""Phase 13 tests — Git Integration + Module Sequencing.

Tests:
  - GitOpsManager: branch, commit, tag, idempotency
  - handle_git_commit: feature branch, commit, tag, git-disabled path
  - handle_module_complete: status update, CommBus event
  - handle_integration_test: pass path, failure-routes-back path
  - handle_next_module: sequencing (deps order), pipeline completion
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_os.comms.bus import AgentCommBus
from agent_os.comms.channels import Channel
from agent_os.config.schema import (
    AgentOSConfig,
    GitConfig,
    OrchestratorConfig,
    ProjectConfig,
    ValidationConfig,
)
from agent_os.git_ops.manager import GitOpsManager, GitResult
from agent_os.orchestrator.context import HandlerContext
from agent_os.orchestrator.handlers import (
    handle_git_commit,
    handle_integration_test,
    handle_module_complete,
    handle_next_module,
)
from agent_os.orchestrator.state import StateManager
from agent_os.storage.database import Database
from agent_os.storage.models import (
    ModuleRecord,
    ModuleStatus,
    PipelineStatus,
)
from agent_os.storage.module_repo import ModuleRepository


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def db():
    d = Database(":memory:")
    d.connect()
    yield d
    d.close()


@pytest.fixture()
def bus():
    return AgentCommBus()


@pytest.fixture()
def state_mgr(db):
    return StateManager(db)


@pytest.fixture()
def tmp_git_repo(tmp_path):
    """Create a real temporary Git repo for GitOpsManager tests."""
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)
    # Initial commit so HEAD exists
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True)
    return repo


def _config_with_git(tmp_path, git_enabled=True) -> AgentOSConfig:
    return AgentOSConfig(
        project=ProjectConfig(root_path=str(tmp_path)),
        git=GitConfig(
            enabled=git_enabled,
            remote="origin",
            main_branch="main",
            dev_branch="dev",
            auto_create_feature_branches=True,
        ),
        orchestrator=OrchestratorConfig(max_iterations_per_module=5),
        validation=ValidationConfig(tests=False),
    )


def _fast_forward_to(state_mgr, target, module_id, iteration):
    """Fast-forward state machine to target status."""
    chains = {
        PipelineStatus.GIT_COMMIT: [
            PipelineStatus.LOADING_REQUIREMENTS,
            PipelineStatus.MODULE_PLANNING,
            PipelineStatus.HITL_1_MODULE_REVIEW,
            PipelineStatus.PROMPT_GENERATION,
            PipelineStatus.HITL_2_PROMPT_REVIEW,
            PipelineStatus.CODE_GENERATION,
            PipelineStatus.VALIDATION,
            PipelineStatus.CODE_REVIEW,
            PipelineStatus.HITL_3_REVIEW_DECISION,
            PipelineStatus.DECISION,
            PipelineStatus.GIT_COMMIT,
        ],
        PipelineStatus.MODULE_COMPLETE: [
            PipelineStatus.LOADING_REQUIREMENTS,
            PipelineStatus.MODULE_PLANNING,
            PipelineStatus.HITL_1_MODULE_REVIEW,
            PipelineStatus.PROMPT_GENERATION,
            PipelineStatus.HITL_2_PROMPT_REVIEW,
            PipelineStatus.CODE_GENERATION,
            PipelineStatus.VALIDATION,
            PipelineStatus.CODE_REVIEW,
            PipelineStatus.HITL_3_REVIEW_DECISION,
            PipelineStatus.DECISION,
            PipelineStatus.GIT_COMMIT,
            PipelineStatus.MODULE_COMPLETE,
        ],
        PipelineStatus.INTEGRATION_TEST: [
            PipelineStatus.LOADING_REQUIREMENTS,
            PipelineStatus.MODULE_PLANNING,
            PipelineStatus.HITL_1_MODULE_REVIEW,
            PipelineStatus.PROMPT_GENERATION,
            PipelineStatus.HITL_2_PROMPT_REVIEW,
            PipelineStatus.CODE_GENERATION,
            PipelineStatus.VALIDATION,
            PipelineStatus.CODE_REVIEW,
            PipelineStatus.HITL_3_REVIEW_DECISION,
            PipelineStatus.DECISION,
            PipelineStatus.GIT_COMMIT,
            PipelineStatus.MODULE_COMPLETE,
            PipelineStatus.HITL_5_PR_REVIEW,
            PipelineStatus.INTEGRATION_TEST,
        ],
        PipelineStatus.NEXT_MODULE: [
            PipelineStatus.LOADING_REQUIREMENTS,
            PipelineStatus.MODULE_PLANNING,
            PipelineStatus.HITL_1_MODULE_REVIEW,
            PipelineStatus.PROMPT_GENERATION,
            PipelineStatus.HITL_2_PROMPT_REVIEW,
            PipelineStatus.CODE_GENERATION,
            PipelineStatus.VALIDATION,
            PipelineStatus.CODE_REVIEW,
            PipelineStatus.HITL_3_REVIEW_DECISION,
            PipelineStatus.DECISION,
            PipelineStatus.GIT_COMMIT,
            PipelineStatus.MODULE_COMPLETE,
            PipelineStatus.HITL_5_PR_REVIEW,
            PipelineStatus.INTEGRATION_TEST,
            PipelineStatus.NEXT_MODULE,
        ],
    }
    for status in chains[target]:
        kw = {}
        if status in (PipelineStatus.PROMPT_GENERATION, PipelineStatus.DECISION,
                       PipelineStatus.GIT_COMMIT):
            kw = {"module_id": module_id, "iteration": iteration}
        state_mgr.transition_to(status, **kw)


def _seed_module(db, module_id, name="Test Module", status=ModuleStatus.PENDING,
                 execution_order=0, deps=None):
    ModuleRepository(db.conn).upsert(ModuleRecord(
        id=module_id,
        name=name,
        feature_name="Test Feature",
        status=status,
        dependency_ids=deps or [],
        execution_order=execution_order,
    ))


# ══════════════════════════════════════════════════════════════════════
#  1. GitOpsManager unit tests (real git repo)
# ══════════════════════════════════════════════════════════════════════


class TestGitOpsManager:

    def test_is_repo(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        assert git.is_repo()

    def test_not_a_repo(self, tmp_path):
        git = GitOpsManager(str(tmp_path))
        assert not git.is_repo()

    def test_current_branch(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        branch = git.current_branch()
        assert branch in ("main", "master")

    def test_create_branch(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        result = git.create_branch("feature/test")
        assert result.success
        assert git.branch_exists("feature/test")

    def test_create_branch_idempotent(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        git.create_branch("feature/test")
        result = git.create_branch("feature/test")
        assert result.success
        assert "already exists" in result.stdout

    def test_create_and_checkout(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        result = git.create_and_checkout("feature/new")
        assert result.success
        assert git.current_branch() == "feature/new"

    def test_checkout_existing(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        git.create_branch("feature/x")
        result = git.checkout("feature/x")
        assert result.success
        assert git.current_branch() == "feature/x"

    def test_commit_all_with_changes(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        (tmp_git_repo / "new_file.py").write_text("print('hello')")
        result = git.commit_all("feat: add new_file")
        assert result.success
        assert "nothing to commit" not in result.stdout

    def test_commit_all_no_changes(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        result = git.commit_all("no-op")
        assert result.success
        assert "nothing to commit" in result.stdout

    def test_has_changes(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        assert not git.has_changes()
        (tmp_git_repo / "dirty.txt").write_text("dirty")
        assert git.has_changes()

    def test_tag(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        result = git.tag("v1.0", "Release 1.0")
        assert result.success

    def test_tag_idempotent(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        git.tag("v1.0", "Release 1.0")
        result = git.tag("v1.0", "Release 1.0")
        assert result.success
        assert "already exists" in result.stdout

    def test_latest_commit_sha(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        sha = git.latest_commit_sha()
        assert sha is not None
        assert len(sha) >= 7

    def test_log_oneline(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        lines = git.log_oneline(3)
        assert len(lines) >= 1
        assert "init" in lines[0]

    def test_git_not_found(self, tmp_path):
        """When git binary is absent, operations return failure gracefully."""
        git = GitOpsManager(str(tmp_path))
        with patch("agent_os.git_ops.manager.subprocess.run", side_effect=FileNotFoundError):
            result = git._run("status")
            assert not result.success
            assert "not found" in result.stderr

    def test_timeout(self, tmp_git_repo):
        git = GitOpsManager(str(tmp_git_repo))
        with patch("agent_os.git_ops.manager.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="git", timeout=60)):
            result = git._run("status")
            assert not result.success
            assert "timed out" in result.stderr


# ══════════════════════════════════════════════════════════════════════
#  2. handle_git_commit tests
# ══════════════════════════════════════════════════════════════════════


class TestHandleGitCommit:

    def _ctx(self, db, bus, config, state_mgr):
        return HandlerContext(state_mgr=state_mgr, db=db, config=config, bus=bus)

    def test_git_commit_with_real_repo(self, db, bus, state_mgr, tmp_git_repo):
        module_id = "mod-auth"
        _seed_module(db, module_id, status=ModuleStatus.IN_PROGRESS)
        config = _config_with_git(tmp_git_repo, git_enabled=True)
        _fast_forward_to(state_mgr, PipelineStatus.GIT_COMMIT, module_id, 1)

        # Create a file to commit
        (tmp_git_repo / "auth.py").write_text("# auth module")

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_git_commit(ctx)

        assert state_mgr.current_status == PipelineStatus.MODULE_COMPLETE

        # Verify commit was made on main branch
        git = GitOpsManager(str(tmp_git_repo))
        assert git.current_branch() == "main"
        assert git.latest_commit_sha() is not None

        # Verify event published
        events = bus.history_for_channel(Channel.PIPELINE_EVENTS)
        assert len(events) >= 1
        assert events[0].payload["event"] == "git_commit"
        assert events[0].payload["branch"] == "main"

    def test_git_disabled_skips_operations(self, db, bus, state_mgr, tmp_path):
        module_id = "mod-auth"
        _seed_module(db, module_id, status=ModuleStatus.IN_PROGRESS)
        config = _config_with_git(tmp_path, git_enabled=False)
        _fast_forward_to(state_mgr, PipelineStatus.GIT_COMMIT, module_id, 1)

        # Create a file so there's something to commit
        (tmp_path / "app.py").write_text("# app")

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_git_commit(ctx)

        assert state_mgr.current_status == PipelineStatus.MODULE_COMPLETE
        events = bus.history_for_channel(Channel.PIPELINE_EVENTS)
        assert events[0].payload["event"] == "git_commit"
        assert events[0].payload["branch"] == "main"
        # No push without github token
        assert events[0].payload["pushed"] is False

    def test_git_commit_no_module_id_raises(self, db, bus, state_mgr, tmp_path):
        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.GIT_COMMIT, None, 1)
        ctx = self._ctx(db, bus, config, state_mgr)
        with pytest.raises(RuntimeError, match="GIT_COMMIT requires"):
            handle_git_commit(ctx)

    def test_git_commit_tags_iteration(self, db, bus, state_mgr, tmp_git_repo):
        module_id = "mod-auth"
        _seed_module(db, module_id, status=ModuleStatus.IN_PROGRESS)
        config = _config_with_git(tmp_git_repo, git_enabled=True)
        _fast_forward_to(state_mgr, PipelineStatus.GIT_COMMIT, module_id, 2)

        (tmp_git_repo / "auth.py").write_text("# auth v2")

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_git_commit(ctx)

        # Verify tag
        git = GitOpsManager(str(tmp_git_repo))
        check = git._run("rev-parse", "refs/tags/mod-auth/v2")
        assert check.success


# ══════════════════════════════════════════════════════════════════════
#  3. handle_module_complete tests
# ══════════════════════════════════════════════════════════════════════


class TestHandleModuleComplete:

    def _ctx(self, db, bus, config, state_mgr):
        return HandlerContext(state_mgr=state_mgr, db=db, config=config, bus=bus)

    def test_marks_module_completed(self, db, bus, state_mgr, tmp_path):
        module_id = "mod-auth"
        _seed_module(db, module_id, status=ModuleStatus.IN_PROGRESS)
        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.MODULE_COMPLETE, module_id, 1)

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_module_complete(ctx)

        # No PR was created (pr_number absent in state), so we skip HITL_5 and go straight to NEXT_MODULE
        assert state_mgr.current_status == PipelineStatus.NEXT_MODULE

        mod = ModuleRepository(db.conn).get(module_id)
        assert mod.status == ModuleStatus.COMPLETED

    def test_publishes_event(self, db, bus, state_mgr, tmp_path):
        module_id = "mod-auth"
        _seed_module(db, module_id, status=ModuleStatus.IN_PROGRESS)
        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.MODULE_COMPLETE, module_id, 3)

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_module_complete(ctx)

        events = bus.history_for_channel(Channel.PIPELINE_EVENTS)
        assert len(events) == 1
        assert events[0].payload["event"] == "module_complete"
        assert events[0].payload["final_iteration"] == 3

    def test_no_module_id_raises(self, db, bus, state_mgr, tmp_path):
        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.MODULE_COMPLETE, None, 1)
        ctx = self._ctx(db, bus, config, state_mgr)
        with pytest.raises(RuntimeError, match="MODULE_COMPLETE requires"):
            handle_module_complete(ctx)


# ══════════════════════════════════════════════════════════════════════
#  4. handle_integration_test tests
# ══════════════════════════════════════════════════════════════════════


class TestHandleIntegrationTest:

    def _ctx(self, db, bus, config, state_mgr):
        return HandlerContext(state_mgr=state_mgr, db=db, config=config, bus=bus)

    @patch("subprocess.run")
    def test_pass_transitions_to_next_module(self, mock_run, db, bus, state_mgr, tmp_path):
        module_id = "mod-auth"
        _seed_module(db, module_id, status=ModuleStatus.IN_PROGRESS)
        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.INTEGRATION_TEST, module_id, 1)

        # compileall succeeds
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_integration_test(ctx)

        assert state_mgr.current_status == PipelineStatus.NEXT_MODULE

        events = bus.history_for_channel(Channel.PIPELINE_EVENTS)
        assert events[0].payload["passed"] is True

    @patch("subprocess.run")
    def test_failure_routes_back_to_prompt_gen(self, mock_run, db, bus, state_mgr, tmp_path):
        module_id = "mod-auth"
        _seed_module(db, module_id, status=ModuleStatus.IN_PROGRESS)
        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.INTEGRATION_TEST, module_id, 1)

        # compileall fails
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="SyntaxError: bad")

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_integration_test(ctx)

        assert state_mgr.current_status == PipelineStatus.PROMPT_GENERATION
        assert state_mgr.state.current_iteration == 2

    def test_no_module_id_raises(self, db, bus, state_mgr, tmp_path):
        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.INTEGRATION_TEST, None, 1)
        ctx = self._ctx(db, bus, config, state_mgr)
        with pytest.raises(RuntimeError, match="INTEGRATION_TEST requires"):
            handle_integration_test(ctx)


# ══════════════════════════════════════════════════════════════════════
#  5. handle_next_module tests
# ══════════════════════════════════════════════════════════════════════


class TestHandleNextModule:

    def _ctx(self, db, bus, config, state_mgr):
        return HandlerContext(state_mgr=state_mgr, db=db, config=config, bus=bus)

    def test_picks_next_pending_module(self, db, bus, state_mgr, tmp_path):
        # M0 completed, M1 pending → picks M1
        _seed_module(db, "M0", name="Foundation", status=ModuleStatus.COMPLETED, execution_order=0)
        _seed_module(db, "M1", name="Auth", status=ModuleStatus.PENDING, execution_order=1, deps=["M0"])
        _seed_module(db, "M2", name="Dashboard", status=ModuleStatus.PENDING, execution_order=2, deps=["M0", "M1"])

        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.NEXT_MODULE, "M0", 1)

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_next_module(ctx)

        assert state_mgr.current_status == PipelineStatus.PROMPT_GENERATION
        assert state_mgr.state.current_module_id == "M1"
        assert state_mgr.state.current_iteration == 1

        # M1 should now be IN_PROGRESS
        m1 = ModuleRepository(db.conn).get("M1")
        assert m1.status == ModuleStatus.IN_PROGRESS

    def test_skips_module_with_unmet_deps(self, db, bus, state_mgr, tmp_path):
        # M0 completed, M1 depends on M0 (met), M2 depends on M1 (not met)
        _seed_module(db, "M0", status=ModuleStatus.COMPLETED, execution_order=0)
        _seed_module(db, "M1", status=ModuleStatus.PENDING, execution_order=1, deps=["M0"])
        _seed_module(db, "M2", status=ModuleStatus.PENDING, execution_order=2, deps=["M1"])

        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.NEXT_MODULE, "M0", 1)

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_next_module(ctx)

        # Should pick M1 (deps met), not M2 (M1 not completed yet)
        assert state_mgr.state.current_module_id == "M1"

    def test_all_modules_completed(self, db, bus, state_mgr, tmp_path):
        _seed_module(db, "M0", status=ModuleStatus.COMPLETED, execution_order=0)
        _seed_module(db, "M1", status=ModuleStatus.COMPLETED, execution_order=1, deps=["M0"])

        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.NEXT_MODULE, "M1", 1)

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_next_module(ctx)

        assert state_mgr.current_status == PipelineStatus.PIPELINE_COMPLETE
        events = bus.history_for_channel(Channel.PIPELINE_EVENTS)
        assert events[0].payload["event"] == "pipeline_complete"
        assert events[0].payload["completed_modules"] == 2

    def test_blocked_pending_modules_complete_pipeline(self, db, bus, state_mgr, tmp_path):
        # M0 completed, M1 depends on M2, M2 depends on M1 (circular → both blocked)
        _seed_module(db, "M0", status=ModuleStatus.COMPLETED, execution_order=0)
        _seed_module(db, "M1", status=ModuleStatus.PENDING, execution_order=1, deps=["M2"])
        _seed_module(db, "M2", status=ModuleStatus.PENDING, execution_order=2, deps=["M1"])

        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.NEXT_MODULE, "M0", 1)

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_next_module(ctx)

        # Circular deps → no module can start → pipeline completes
        assert state_mgr.current_status == PipelineStatus.PIPELINE_COMPLETE

    def test_no_deps_module_picked_first(self, db, bus, state_mgr, tmp_path):
        # M0 has no deps, M1 depends on M0 — both pending
        _seed_module(db, "M0", status=ModuleStatus.PENDING, execution_order=0, deps=[])
        _seed_module(db, "M1", status=ModuleStatus.PENDING, execution_order=1, deps=["M0"])

        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.NEXT_MODULE, None, 0)

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_next_module(ctx)

        assert state_mgr.state.current_module_id == "M0"

    def test_publishes_next_module_event(self, db, bus, state_mgr, tmp_path):
        _seed_module(db, "M0", status=ModuleStatus.COMPLETED, execution_order=0)
        _seed_module(db, "M1", name="Auth Module", status=ModuleStatus.PENDING, execution_order=1, deps=["M0"])

        config = _config_with_git(tmp_path)
        _fast_forward_to(state_mgr, PipelineStatus.NEXT_MODULE, "M0", 1)

        ctx = self._ctx(db, bus, config, state_mgr)
        handle_next_module(ctx)

        events = bus.history_for_channel(Channel.PIPELINE_EVENTS)
        assert len(events) == 1
        assert events[0].payload["event"] == "next_module"
        assert events[0].payload["module_id"] == "M1"
        assert events[0].payload["module_name"] == "Auth Module"
        assert events[0].payload["remaining"] == 0


# ══════════════════════════════════════════════════════════════════════
#  6. Integration: Full module lifecycle
# ══════════════════════════════════════════════════════════════════════


class TestModuleLifecycleIntegration:
    """GIT_COMMIT → MODULE_COMPLETE → NEXT_MODULE (no PR) → PIPELINE_COMPLETE."""

    def _ctx(self, db, bus, config, state_mgr):
        return HandlerContext(state_mgr=state_mgr, db=db, config=config, bus=bus)

    def test_full_lifecycle_to_pipeline_complete(
        self, db, bus, state_mgr, tmp_git_repo
    ):
        # Setup: M0 completed, M1 in-progress (being committed)
        _seed_module(db, "M0", status=ModuleStatus.COMPLETED, execution_order=0)
        _seed_module(db, "M1", status=ModuleStatus.IN_PROGRESS, execution_order=1, deps=["M0"])

        config = _config_with_git(tmp_git_repo, git_enabled=True)
        _fast_forward_to(state_mgr, PipelineStatus.GIT_COMMIT, "M1", 2)

        # Add a file to commit
        (tmp_git_repo / "feature.py").write_text("# M1 feature code")

        ctx = self._ctx(db, bus, config, state_mgr)

        # Step 1: GIT_COMMIT → MODULE_COMPLETE
        handle_git_commit(ctx)
        assert state_mgr.current_status == PipelineStatus.MODULE_COMPLETE

        # Step 2: MODULE_COMPLETE → NEXT_MODULE (no pr_number in state, skip HITL_5)
        handle_module_complete(ctx)
        assert state_mgr.current_status == PipelineStatus.NEXT_MODULE

        # Step 3: No more pending modules → PIPELINE_COMPLETE
        handle_next_module(ctx)
        assert state_mgr.current_status == PipelineStatus.PIPELINE_COMPLETE

        # Verify M1 is marked completed
        m1 = ModuleRepository(db.conn).get("M1")
        assert m1.status == ModuleStatus.COMPLETED

        # Verify CommBus has all events
        events = bus.history_for_channel(Channel.PIPELINE_EVENTS)
        event_types = [e.payload.get("event") for e in events]
        assert "git_commit" in event_types
        assert "module_complete" in event_types
        assert "pipeline_complete" in event_types
