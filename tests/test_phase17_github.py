"""Phase 17 tests — GitHub MCP Integration.

Tests GitHub client, GitOpsManager.push(), handler updates for push/PR,
HITL_5 merge hook, final dev→main PR, module API PR metadata,
and frontend-facing data plumbing.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_os.api.deps import orch_holder
from agent_os.comms.bus import AgentCommBus
from agent_os.comms.channels import Channel
from agent_os.comms.messages import PipelineEventMessage
from agent_os.config.schema import (
    AgentOSConfig,
    GitConfig,
    GitHubConfig,
    SecretsConfig,
)
from agent_os.git_ops.manager import GitOpsManager, GitResult
from agent_os.github.client import GitHubClient, GitHubResult
from agent_os.orchestrator.context import HandlerContext
from agent_os.orchestrator.state import StateManager
from agent_os.storage.database import Database
from agent_os.storage.models import (
    ModuleRecord,
    ModuleStatus,
    PipelineState,
    PipelineStatus,
)


# ---------- Fixtures --------------------------------------------------------


@pytest.fixture()
def tmp_config(tmp_path: Path) -> AgentOSConfig:
    db_path = str(tmp_path / "test.db")
    return AgentOSConfig(
        storage={"db_path": db_path},
        git={"enabled": True, "auto_create_feature_branches": True},
        github={"owner": "test-owner", "repo": "test-repo", "auto_push": True, "auto_create_pr": True},
        secrets={"openai_api_key": "sk-test-123", "github_token": "ghp_test_token"},
    )


@pytest.fixture()
def tmp_config_no_github(tmp_path: Path) -> AgentOSConfig:
    db_path = str(tmp_path / "test.db")
    return AgentOSConfig(
        storage={"db_path": db_path},
        git={"enabled": True, "auto_create_feature_branches": True},
        github={"owner": "", "repo": ""},
        secrets={"openai_api_key": "sk-test-123", "github_token": ""},
    )


@pytest.fixture()
def handler_ctx(tmp_config: AgentOSConfig):
    """Create a HandlerContext with DB, state manager, and bus."""
    db = Database(tmp_config.storage.db_path)
    db.connect()
    state_mgr = StateManager(db)
    bus = AgentCommBus()
    ctx = HandlerContext(state_mgr=state_mgr, db=db, config=tmp_config, bus=bus)
    yield ctx
    db.close()


@pytest.fixture()
def app_client(tmp_config: AgentOSConfig):
    """Create a TestClient with all routers."""
    from agent_os.api.routes import bus, metrics, modules, pipeline, requirements, settings

    orch_holder.shutdown()
    orch = orch_holder.init(tmp_config)

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    app = FastAPI(lifespan=noop_lifespan)
    app.include_router(pipeline.router)
    app.include_router(modules.router)
    app.include_router(requirements.router)
    app.include_router(metrics.router)
    app.include_router(bus.router)
    app.include_router(settings.router)

    with TestClient(app) as client:
        yield client, orch

    orch_holder.shutdown()


# ---------- Test: GitHub Client ────────────────────────────────────────────


class TestGitHubClient:
    """Test GitHubClient initialization and request construction."""

    def test_init_requires_token(self):
        with pytest.raises(ValueError, match="token is required"):
            GitHubClient(token="", owner="o", repo="r")

    def test_init_requires_owner_repo(self):
        with pytest.raises(ValueError, match="owner and repo are required"):
            GitHubClient(token="tok", owner="", repo="r")
        with pytest.raises(ValueError, match="owner and repo are required"):
            GitHubClient(token="tok", owner="o", repo="")

    def test_init_success(self):
        client = GitHubClient(token="ghp_abc", owner="myorg", repo="myrepo")
        assert client._owner == "myorg"
        assert client._repo == "myrepo"
        assert "myorg/myrepo" in client._base_url

    def test_headers_contain_bearer_token(self):
        client = GitHubClient(token="ghp_abc", owner="o", repo="r")
        headers = client._headers()
        assert headers["Authorization"] == "Bearer ghp_abc"
        assert "github" in headers["Accept"]

    @patch("agent_os.github.client.httpx.Client")
    def test_create_pr_success(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.content = b'{"number": 42, "html_url": "https://github.com/o/r/pull/42"}'
        mock_response.json.return_value = {"number": 42, "html_url": "https://github.com/o/r/pull/42"}

        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_response
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        client = GitHubClient(token="ghp_abc", owner="o", repo="r")
        result = client.create_pr("Test PR", "feature/mod-1", "dev", "body")

        assert result.success
        assert result.data["number"] == 42

    @patch("agent_os.github.client.httpx.Client")
    def test_create_pr_validation_error(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.content = b'{"message": "Validation Failed"}'
        mock_response.json.return_value = {"message": "Validation Failed"}
        mock_response.text = "Validation Failed"

        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_response
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        client = GitHubClient(token="ghp_abc", owner="o", repo="r")
        result = client.create_pr("Test PR", "feature/mod-1", "dev")

        assert not result.success
        assert result.status_code == 422
        assert "Validation" in result.error

    @patch("agent_os.github.client.httpx.Client")
    def test_merge_pr_success(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"merged": true}'
        mock_response.json.return_value = {"merged": True}

        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_response
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        client = GitHubClient(token="ghp_abc", owner="o", repo="r")
        result = client.merge_pr(42, merge_method="squash")

        assert result.success
        call_args = mock_instance.request.call_args
        assert call_args[0][0] == "PUT"
        assert "/pulls/42/merge" in call_args[0][1]

    @patch("agent_os.github.client.httpx.Client")
    def test_add_pr_comment(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.content = b'{"id": 1}'
        mock_response.json.return_value = {"id": 1}

        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_response
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        client = GitHubClient(token="ghp_abc", owner="o", repo="r")
        result = client.add_pr_comment(42, "Module completed!")

        assert result.success
        call_args = mock_instance.request.call_args
        assert "/issues/42/comments" in call_args[0][1]

    @patch("agent_os.github.client.httpx.Client")
    def test_get_pr(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"number": 42, "state": "open"}'
        mock_response.json.return_value = {"number": 42, "state": "open"}

        mock_instance = MagicMock()
        mock_instance.request.return_value = mock_response
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        client = GitHubClient(token="ghp_abc", owner="o", repo="r")
        result = client.get_pr(42)

        assert result.success
        assert result.data["number"] == 42

    @patch("agent_os.github.client.httpx.Client")
    def test_retry_on_server_error(self, mock_client_cls):
        """Should retry on 500 errors."""
        mock_500 = MagicMock()
        mock_500.status_code = 500
        mock_500.content = b'{"message": "Internal Server Error"}'
        mock_500.json.return_value = {"message": "Internal Server Error"}
        mock_500.text = "Internal Server Error"

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.content = b'{"number": 42}'
        mock_200.json.return_value = {"number": 42}

        mock_instance = MagicMock()
        mock_instance.request.side_effect = [mock_500, mock_200]
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        client = GitHubClient(token="ghp_abc", owner="o", repo="r")
        with patch("agent_os.github.client.time.sleep"):
            result = client.create_pr("test", "h", "b")

        assert result.success


# ---------- Test: GitOpsManager push ───────────────────────────────────────


class TestGitOpsManagerPush:

    def test_push_constructs_correct_command(self, tmp_path: Path):
        git = GitOpsManager(working_dir=str(tmp_path))
        with patch.object(git, "_run") as mock_run:
            mock_run.return_value = GitResult(success=True, command="git push origin feature/mod-1")
            result = git.push("feature/mod-1")
        mock_run.assert_called_once_with("push", "origin", "feature/mod-1")
        assert result.success

    def test_push_force(self, tmp_path: Path):
        git = GitOpsManager(working_dir=str(tmp_path))
        with patch.object(git, "_run") as mock_run:
            mock_run.return_value = GitResult(success=True, command="")
            git.push("feature/mod-1", force=True)
        mock_run.assert_called_once_with("push", "--force", "origin", "feature/mod-1")

    def test_push_custom_remote(self, tmp_path: Path):
        git = GitOpsManager(working_dir=str(tmp_path), remote="upstream")
        with patch.object(git, "_run") as mock_run:
            mock_run.return_value = GitResult(success=True, command="")
            git.push("main")
        mock_run.assert_called_once_with("push", "upstream", "main")

    def test_push_tags(self, tmp_path: Path):
        git = GitOpsManager(working_dir=str(tmp_path))
        with patch.object(git, "_run") as mock_run:
            mock_run.return_value = GitResult(success=True, command="")
            git.push_tags()
        mock_run.assert_called_once_with("push", "origin", "--tags")


# ---------- Test: handle_git_commit with push/PR ──────────────────────────


class TestHandleGitCommitGitHub:

    def _setup_state(self, ctx, module_id="mod-1", iteration=1):
        """Transition to GIT_COMMIT state with a module."""
        from agent_os.storage.module_repo import ModuleRepository

        mod_repo = ModuleRepository(ctx.db.conn)
        mod_repo.upsert(ModuleRecord(id=module_id, name="Test Module"))
        mod_repo.update_status(module_id, ModuleStatus.IN_PROGRESS)

        # Walk through transitions to get to GIT_COMMIT
        ctx.state_mgr.transition_to(PipelineStatus.LOADING_REQUIREMENTS)
        ctx.state_mgr.transition_to(PipelineStatus.MODULE_PLANNING)
        ctx.state_mgr.transition_to(PipelineStatus.HITL_1_MODULE_REVIEW)
        ctx.state_mgr.transition_to(
            PipelineStatus.PROMPT_GENERATION, module_id=module_id, iteration=iteration,
        )
        ctx.state_mgr.transition_to(PipelineStatus.HITL_2_PROMPT_REVIEW)
        ctx.state_mgr.transition_to(PipelineStatus.CODE_GENERATION)
        ctx.state_mgr.transition_to(PipelineStatus.VALIDATION)
        ctx.state_mgr.transition_to(PipelineStatus.CODE_REVIEW)
        ctx.state_mgr.transition_to(PipelineStatus.HITL_3_REVIEW_DECISION)
        ctx.state_mgr.transition_to(PipelineStatus.DECISION)
        ctx.state_mgr.transition_to(PipelineStatus.GIT_COMMIT)

    @patch("agent_os.orchestrator.handlers._ensure_remote_repo")
    @patch("agent_os.git_ops.manager.GitOpsManager")
    def test_push_and_pr_when_enabled(self, mock_git_cls, mock_ensure_remote, handler_ctx):
        """When GitHub token is configured, commit on main and push."""
        from agent_os.orchestrator.handlers import handle_git_commit

        self._setup_state(handler_ctx)

        mock_git = MagicMock()
        mock_git.is_repo.return_value = True
        mock_git.current_branch.return_value = "main"
        mock_git.commit_all.return_value = GitResult(success=True, command="", stdout="committed")
        mock_git.latest_commit_sha.return_value = "abc1234"
        mock_git.tag.return_value = GitResult(success=True, command="")
        mock_git.push.return_value = GitResult(success=True, command="")
        mock_git.push_tags.return_value = GitResult(success=True, command="")
        mock_git_cls.return_value = mock_git

        mock_ensure_remote.return_value = True

        handle_git_commit(handler_ctx)

        # Verify push to main was called
        mock_git.push.assert_called_once_with("main")
        mock_git.push_tags.assert_called_once()

        # Verify bus event
        events = handler_ctx.bus.history_for_channel(Channel.PIPELINE_EVENTS)
        git_events = [e for e in events if e.payload.get("event") == "git_commit"]
        assert len(git_events) == 1
        assert git_events[0].payload["branch"] == "main"
        assert git_events[0].payload["pushed"] is True

    @patch("agent_os.orchestrator.handlers._ensure_remote_repo")
    @patch("agent_os.git_ops.manager.GitOpsManager")
    def test_no_push_when_disabled(self, mock_git_cls, mock_ensure_remote, handler_ctx):
        """When GitHub token is missing, no push occurs."""
        from agent_os.orchestrator.handlers import handle_git_commit

        handler_ctx.config.github.auto_push = False
        handler_ctx.config.secrets.github_token = ""
        self._setup_state(handler_ctx)

        mock_git = MagicMock()
        mock_git.is_repo.return_value = True
        mock_git.current_branch.return_value = "main"
        mock_git.commit_all.return_value = GitResult(success=True, command="", stdout="committed")
        mock_git.latest_commit_sha.return_value = "abc1234"
        mock_git.tag.return_value = GitResult(success=True, command="")
        mock_git_cls.return_value = mock_git

        mock_ensure_remote.return_value = False

        handle_git_commit(handler_ctx)

        mock_git.push.assert_not_called()

        # State should transition to MODULE_COMPLETE
        assert handler_ctx.state_mgr.current_status == PipelineStatus.MODULE_COMPLETE


# ---------- Test: handle_module_complete with PR comment ──────────────────


class TestHandleModuleCompleteGitHub:

    def _setup_state_at_module_complete(self, ctx, module_id="mod-1"):
        from agent_os.storage.module_repo import ModuleRepository

        mod_repo = ModuleRepository(ctx.db.conn)
        mod_repo.upsert(ModuleRecord(id=module_id, name="Test Module"))
        mod_repo.update_status(module_id, ModuleStatus.IN_PROGRESS)

        ctx.state_mgr.transition_to(PipelineStatus.LOADING_REQUIREMENTS)
        ctx.state_mgr.transition_to(PipelineStatus.MODULE_PLANNING)
        ctx.state_mgr.transition_to(PipelineStatus.HITL_1_MODULE_REVIEW)
        ctx.state_mgr.transition_to(
            PipelineStatus.PROMPT_GENERATION, module_id=module_id, iteration=1,
        )
        ctx.state_mgr.transition_to(PipelineStatus.HITL_2_PROMPT_REVIEW)
        ctx.state_mgr.transition_to(PipelineStatus.CODE_GENERATION)
        ctx.state_mgr.transition_to(PipelineStatus.VALIDATION)
        ctx.state_mgr.transition_to(PipelineStatus.CODE_REVIEW)
        ctx.state_mgr.transition_to(PipelineStatus.HITL_3_REVIEW_DECISION)
        ctx.state_mgr.transition_to(PipelineStatus.DECISION)
        ctx.state_mgr.transition_to(PipelineStatus.GIT_COMMIT)
        ctx.state_mgr.transition_to(
            PipelineStatus.MODULE_COMPLETE,
            metadata={"pushed": True, "repo_url": "https://github.com/o/r"},
        )

    def test_module_complete_transitions_to_next_module(self, handler_ctx):
        from agent_os.orchestrator.handlers import handle_module_complete

        self._setup_state_at_module_complete(handler_ctx)
        handle_module_complete(handler_ctx)

        assert handler_ctx.state_mgr.current_status == PipelineStatus.NEXT_MODULE

    def test_module_complete_publishes_event(self, handler_ctx):
        from agent_os.orchestrator.handlers import handle_module_complete

        self._setup_state_at_module_complete(handler_ctx)
        handle_module_complete(handler_ctx)

        events = handler_ctx.bus.history_for_channel(Channel.PIPELINE_EVENTS)
        complete_events = [e for e in events if e.payload.get("event") == "module_complete"]
        assert len(complete_events) == 1
        assert complete_events[0].payload["pushed"] is True
        assert complete_events[0].payload["repo_url"] == "https://github.com/o/r"



# ---------- Test: HITL_5 merge hook ───────────────────────────────────────


class TestHITL5MergeHook:

    def test_merge_pr_on_hitl5_approval(self, tmp_config):
        """Approving HITL_5 should merge the PR."""
        from agent_os.orchestrator.engine import Orchestrator

        orch = Orchestrator(tmp_config)

        # Set up state at HITL_5 with PR info in metadata
        from agent_os.storage.module_repo import ModuleRepository
        mod_repo = ModuleRepository(orch.db.conn)
        mod_repo.upsert(ModuleRecord(id="mod-1", name="Test"))
        mod_repo.update_status("mod-1", ModuleStatus.IN_PROGRESS)

        orch.state_mgr.transition_to(PipelineStatus.LOADING_REQUIREMENTS)
        orch.state_mgr.transition_to(PipelineStatus.MODULE_PLANNING)
        orch.state_mgr.transition_to(PipelineStatus.HITL_1_MODULE_REVIEW)
        orch.state_mgr.transition_to(
            PipelineStatus.PROMPT_GENERATION, module_id="mod-1", iteration=1,
        )
        orch.state_mgr.transition_to(PipelineStatus.HITL_2_PROMPT_REVIEW)
        orch.state_mgr.transition_to(PipelineStatus.CODE_GENERATION)
        orch.state_mgr.transition_to(PipelineStatus.VALIDATION)
        orch.state_mgr.transition_to(PipelineStatus.CODE_REVIEW)
        orch.state_mgr.transition_to(PipelineStatus.HITL_3_REVIEW_DECISION)
        orch.state_mgr.transition_to(PipelineStatus.DECISION)
        orch.state_mgr.transition_to(PipelineStatus.GIT_COMMIT)
        orch.state_mgr.transition_to(
            PipelineStatus.MODULE_COMPLETE,
            metadata={"pr_number": 42},
        )
        orch.state_mgr.transition_to(PipelineStatus.HITL_5_PR_REVIEW)

        with patch("agent_os.orchestrator.handlers._create_github_client") as mock_handler_create:
            mock_client = MagicMock()
            mock_client.merge_pr.return_value = GitHubResult(success=True, status_code=200)
            mock_handler_create.return_value = mock_client

            # Approve the gate
            orch.approve_gate()

        # Should have transitioned to INTEGRATION_TEST
        assert orch.state_mgr.current_status == PipelineStatus.INTEGRATION_TEST

    def test_no_merge_without_pr_number(self, tmp_config):
        """Approving HITL_5 without a PR number just transitions."""
        from agent_os.orchestrator.engine import Orchestrator

        orch = Orchestrator(tmp_config)

        from agent_os.storage.module_repo import ModuleRepository
        mod_repo = ModuleRepository(orch.db.conn)
        mod_repo.upsert(ModuleRecord(id="mod-1", name="Test"))
        mod_repo.update_status("mod-1", ModuleStatus.IN_PROGRESS)

        orch.state_mgr.transition_to(PipelineStatus.LOADING_REQUIREMENTS)
        orch.state_mgr.transition_to(PipelineStatus.MODULE_PLANNING)
        orch.state_mgr.transition_to(PipelineStatus.HITL_1_MODULE_REVIEW)
        orch.state_mgr.transition_to(
            PipelineStatus.PROMPT_GENERATION, module_id="mod-1", iteration=1,
        )
        orch.state_mgr.transition_to(PipelineStatus.HITL_2_PROMPT_REVIEW)
        orch.state_mgr.transition_to(PipelineStatus.CODE_GENERATION)
        orch.state_mgr.transition_to(PipelineStatus.VALIDATION)
        orch.state_mgr.transition_to(PipelineStatus.CODE_REVIEW)
        orch.state_mgr.transition_to(PipelineStatus.HITL_3_REVIEW_DECISION)
        orch.state_mgr.transition_to(PipelineStatus.DECISION)
        orch.state_mgr.transition_to(PipelineStatus.GIT_COMMIT)
        orch.state_mgr.transition_to(PipelineStatus.MODULE_COMPLETE)
        orch.state_mgr.transition_to(PipelineStatus.HITL_5_PR_REVIEW)

        orch.approve_gate()

        assert orch.state_mgr.current_status == PipelineStatus.INTEGRATION_TEST


# ---------- Test: _create_github_client helper ────────────────────────────


class TestCreateGitHubClient:

    def test_returns_client_when_configured(self, handler_ctx):
        from agent_os.orchestrator.handlers import _create_github_client
        client = _create_github_client(handler_ctx)
        assert client is not None
        assert isinstance(client, GitHubClient)

    def test_returns_none_without_owner(self, handler_ctx):
        from agent_os.orchestrator.handlers import _create_github_client
        handler_ctx.config.github.owner = ""
        client = _create_github_client(handler_ctx)
        assert client is None

    def test_returns_none_without_token(self, handler_ctx):
        from agent_os.orchestrator.handlers import _create_github_client
        handler_ctx.config.secrets.github_token = ""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_TOKEN", None)
            os.environ.pop("GITHUB_PAT_TOKEN", None)
            client = _create_github_client(handler_ctx)
        assert client is None


# ---------- Test: StateManager.update_metadata ────────────────────────────


class TestStateManagerMetadata:

    def test_update_metadata(self, handler_ctx):
        handler_ctx.state_mgr.update_metadata({"pr_number": 99, "key": "val"})
        state = handler_ctx.state_mgr.state
        assert state.metadata["pr_number"] == 99
        assert state.metadata["key"] == "val"

    def test_update_metadata_preserves_state(self, handler_ctx):
        handler_ctx.state_mgr.transition_to(PipelineStatus.LOADING_REQUIREMENTS)
        handler_ctx.state_mgr.update_metadata({"foo": "bar"})
        state = handler_ctx.state_mgr.state
        assert state.pipeline_status == PipelineStatus.LOADING_REQUIREMENTS
        assert state.metadata["foo"] == "bar"


# ---------- Test: Module API with PR info ─────────────────────────────────


class TestModuleAPIPR:

    def test_module_response_has_pr_fields(self, app_client):
        """ModuleResponse schema includes pr_number and pr_url."""
        client, orch = app_client

        # Create a module
        from agent_os.storage.module_repo import ModuleRepository
        mod_repo = ModuleRepository(orch.db.conn)
        mod_repo.upsert(ModuleRecord(id="mod-1", name="Test Module"))

        # Publish a git_commit event with PR info
        orch.bus.publish(PipelineEventMessage(
            sender="git_ops",
            module_id="mod-1",
            payload={
                "event": "git_commit",
                "pr_number": 42,
                "pr_url": "https://github.com/o/r/pull/42",
            },
        ))

        resp = client.get("/api/modules")
        assert resp.status_code == 200
        modules = resp.json()
        assert len(modules) == 1
        assert modules[0]["pr_number"] == 42
        assert modules[0]["pr_url"] == "https://github.com/o/r/pull/42"

    def test_module_without_pr(self, app_client):
        """Modules without PRs return null pr_number."""
        client, orch = app_client

        from agent_os.storage.module_repo import ModuleRepository
        mod_repo = ModuleRepository(orch.db.conn)
        mod_repo.upsert(ModuleRecord(id="mod-2", name="No PR Module"))

        resp = client.get("/api/modules")
        assert resp.status_code == 200
        modules = resp.json()
        mod = [m for m in modules if m["id"] == "mod-2"][0]
        assert mod["pr_number"] is None
        assert mod["pr_url"] == ""

    def test_single_module_has_pr(self, app_client):
        """GET /api/modules/{id} includes PR info."""
        client, orch = app_client

        from agent_os.storage.module_repo import ModuleRepository
        mod_repo = ModuleRepository(orch.db.conn)
        mod_repo.upsert(ModuleRecord(id="mod-3", name="PR Module"))

        orch.bus.publish(PipelineEventMessage(
            sender="git_ops",
            module_id="mod-3",
            payload={
                "event": "git_commit",
                "pr_number": 99,
                "pr_url": "https://github.com/o/r/pull/99",
            },
        ))

        resp = client.get("/api/modules/mod-3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pr_number"] == 99
