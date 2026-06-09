"""Tests for Custom Input Modes — Phases 1-4 (Plan: PLAN_custom_input_modes.md).

Covers:
  5.1-A  Requirements file upload/select endpoints (Phase 1)
  5.1-B  GitHub fork & clone capabilities (Phase 2)
  5.1-C  Initial code review construction (Phase 3)
  5.1-D  Prompt Generator full-repo-review handling (Phase 4)
  5.1-E  Pipeline state-machine transitions for github_review mode
  5.1-F  handle_next_module short-circuit in github_review mode
  5.1-G  Edge cases: no token, invalid URL, clone failure
"""

from __future__ import annotations

import json
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_os.api.deps import orch_holder
from agent_os.comms.bus import AgentCommBus
from agent_os.config.schema import AgentOSConfig, GitHubReviewConfig
from agent_os.orchestrator.context import HandlerContext
from agent_os.orchestrator.state import StateManager
from agent_os.storage.database import Database
from agent_os.storage.models import PipelineState, PipelineStatus


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_config(tmp_path: Path) -> AgentOSConfig:
    db_path = str(tmp_path / "test.db")
    return AgentOSConfig(
        storage={"db_path": db_path},
        github={"owner": "test-owner", "repo": "test-repo"},
        secrets={"openai_api_key": "sk-test", "github_token": "ghp_test"},
        github_review={
            "source_repo_url": "https://github.com/owner/my-repo",
            "requirements_path": "",
            "fork_repo_name": "",
            "branch_name": "agent-os-fixes",
        },
        pipeline_mode="standard",
    )


@pytest.fixture()
def handler_ctx(tmp_config: AgentOSConfig, tmp_path: Path):
    db = Database(tmp_config.storage.db_path)
    db.connect()
    state_mgr = StateManager(db)
    bus = AgentCommBus()
    ctx = HandlerContext(state_mgr=state_mgr, db=db, config=tmp_config, bus=bus)
    yield ctx
    db.close()


@pytest.fixture()
def app_client(tmp_config: AgentOSConfig):
    from agent_os.api.routes import bus, metrics, modules, pipeline, requirements, settings

    orch_holder.shutdown()
    orch_holder.init(tmp_config)

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    app = FastAPI(lifespan=noop_lifespan)
    app.include_router(pipeline.router)
    app.include_router(requirements.router)
    app.include_router(settings.router)
    app.include_router(modules.router)
    app.include_router(metrics.router)
    app.include_router(bus.router)

    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# 5.1-A  Requirements upload / select (Phase 1)
# ---------------------------------------------------------------------------


class TestRequirementsUpload:
    VALID_YAML = textwrap.dedent("""\
        epics:
          - id: E1
            title: User Auth
            features:
              - id: F1
                title: Login
                stories:
                  - id: S1
                    title: As a user I can log in
                    acceptance_criteria:
                      - id: AC1
                        description: Valid credentials grant access
    """)

    def test_upload_valid_yaml(self, app_client: TestClient, tmp_path: Path):
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text(self.VALID_YAML)

        with open(req_file, "rb") as fh:
            resp = app_client.post(
                "/api/requirements/upload",
                files={"file": ("requirements.yaml", fh, "application/x-yaml")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["stats"]["epics"] == 1
        assert data["stats"]["features"] == 1
        assert data["stats"]["stories"] == 1
        assert data["stats"]["acceptance_criteria"] == 1

    def test_upload_invalid_yaml_returns_422(self, app_client: TestClient, tmp_path: Path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("this: is: not: valid: yaml: {{{}}")

        with open(bad_file, "rb") as fh:
            resp = app_client.post(
                "/api/requirements/upload",
                files={"file": ("bad.yaml", fh, "application/x-yaml")},
            )

        # Should 422 — invalid schema
        assert resp.status_code in (422, 400)

    def test_upload_wrong_extension_rejected(self, app_client: TestClient, tmp_path: Path):
        txt_file = tmp_path / "requirements.txt"
        txt_file.write_text("not yaml")

        with open(txt_file, "rb") as fh:
            resp = app_client.post(
                "/api/requirements/upload",
                files={"file": ("requirements.txt", fh, "text/plain")},
            )

        assert resp.status_code in (400, 422)

    def test_select_existing_valid_file(self, app_client: TestClient, tmp_path: Path):
        req_file = tmp_path / "requirements.yaml"
        req_file.write_text(self.VALID_YAML)

        resp = app_client.post(
            "/api/requirements/select",
            json={"path": str(req_file)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_select_nonexistent_file_returns_error(self, app_client: TestClient):
        resp = app_client.post(
            "/api/requirements/select",
            json={"path": "/does/not/exist.yaml"},
        )
        assert resp.status_code in (400, 404, 422)


# ---------------------------------------------------------------------------
# 5.1-B  GitHub fork & clone (Phase 2)
# ---------------------------------------------------------------------------


class TestForkAndClone:
    def test_fork_repo_success(self):
        from agent_os.github.client import GitHubClient, GitHubResult

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 202
            mock_resp.content = b'{"full_name":"authenticated-user/my-repo"}'
            mock_resp.json.return_value = {"full_name": "authenticated-user/my-repo"}
            mock_client_cls.return_value.__enter__.return_value.request.return_value = mock_resp

            client = GitHubClient(token="ghp_test", owner="owner", repo="my-repo")
            result = client.fork_repo("owner", "my-repo")

        assert result.success is True
        assert result.data["full_name"] == "authenticated-user/my-repo"

    def test_fork_repo_already_exists_treated_as_success(self):
        from agent_os.github.client import GitHubClient

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 202
            mock_resp.content = b'{"full_name":"user/my-repo"}'
            mock_resp.json.return_value = {"full_name": "user/my-repo"}
            mock_client_cls.return_value.__enter__.return_value.request.return_value = mock_resp

            client = GitHubClient(token="ghp_test", owner="owner", repo="my-repo")
            result = client.fork_repo("owner", "my-repo")

        assert result.success is True

    def test_get_authenticated_user(self):
        from agent_os.github.client import GitHubClient

        with patch("httpx.Client") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b'{"login":"my-user"}'
            mock_resp.json.return_value = {"login": "my-user"}
            mock_client_cls.return_value.__enter__.return_value.request.return_value = mock_resp

            client = GitHubClient(token="ghp_test", owner="owner", repo="repo")
            result = client.get_authenticated_user()

        assert result.success is True
        assert result.data["login"] == "my-user"

    def test_clone_static_method(self, tmp_path: Path):
        from agent_os.git_ops.manager import GitOpsManager

        dest = str(tmp_path / "cloned-repo")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = GitOpsManager.clone(
                "https://x-access-token:ghp_test@github.com/user/repo.git", dest
            )

        assert result.success is True
        # Token should be redacted from the logged command
        assert "ghp_test" not in str(mock_run.call_args)

    def test_clone_failure_returns_error(self, tmp_path: Path):
        from agent_os.git_ops.manager import GitOpsManager

        dest = str(tmp_path / "failed-clone")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=128, stdout="", stderr="repository not found"
            )
            result = GitOpsManager.clone(
                "https://x-access-token:ghp_test@github.com/user/no-exist.git", dest
            )

        assert result.success is False
        assert result.stderr != ""

    def test_handle_github_fork_clone_no_token_raises(self, handler_ctx: HandlerContext):
        from agent_os.orchestrator.handlers import handle_github_fork_clone

        handler_ctx.config.secrets.github_token = ""
        handler_ctx.config.pipeline_mode = "github_review"

        import os
        with patch.dict(os.environ, {}, clear=True):
            # Remove GITHUB_TOKEN / GH_TOKEN from env
            for key in ("GITHUB_TOKEN", "GH_TOKEN"):
                os.environ.pop(key, None)
            with pytest.raises(RuntimeError, match="GitHub token is required"):
                handle_github_fork_clone(handler_ctx)

    def test_handle_github_fork_clone_invalid_url_raises(self, handler_ctx: HandlerContext):
        from agent_os.orchestrator.handlers import handle_github_fork_clone

        handler_ctx.config.github_review.source_repo_url = "not-a-github-url"
        handler_ctx.config.pipeline_mode = "github_review"

        with pytest.raises(RuntimeError, match="Cannot parse owner/repo"):
            handle_github_fork_clone(handler_ctx)

    def test_handle_github_fork_clone_no_url_raises(self, handler_ctx: HandlerContext):
        from agent_os.orchestrator.handlers import handle_github_fork_clone

        handler_ctx.config.github_review.source_repo_url = ""
        handler_ctx.config.pipeline_mode = "github_review"

        with pytest.raises(RuntimeError, match="source_repo_url is not configured"):
            handle_github_fork_clone(handler_ctx)


# ---------------------------------------------------------------------------
# 5.1-C  Initial code review construction (Phase 3)
# ---------------------------------------------------------------------------


class TestInitialCodeReview:
    def test_collect_repo_files(self, tmp_path: Path):
        from agent_os.orchestrator.handlers import _collect_repo_files

        # Create a small fake repo tree
        (tmp_path / "main.py").write_text("# main")
        (tmp_path / "utils.py").write_text("# utils")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"bytecode")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg").mkdir()
        (tmp_path / "node_modules" / "pkg" / "index.js").write_text("// excluded")

        files = _collect_repo_files(str(tmp_path))

        relative_names = {Path(f).name for f in files}
        assert "main.py" in relative_names
        assert "utils.py" in relative_names
        # Excluded dirs must not appear
        assert "main.cpython-311.pyc" not in relative_names
        assert "index.js" not in relative_names

    def test_collect_repo_files_respects_max(self, tmp_path: Path):
        from agent_os.orchestrator.handlers import _collect_repo_files

        for i in range(20):
            (tmp_path / f"file_{i}.py").write_text(f"# {i}")

        files = _collect_repo_files(str(tmp_path), max_files=5)
        assert len(files) == 5

    def test_run_initial_review_uses_initial_prompt(self):
        from agent_os.code_reviewer.runner import (
            CodeReviewerRunner,
            _INITIAL_REVIEW_SYSTEM_PROMPT,
        )
        from agent_os.module_maker.schema import ModuleDefinition

        cfg = AgentOSConfig(
            secrets={"openai_api_key": "sk-test"},
            project={"root_path": "."},
        )

        captured_calls: list[dict] = []

        def fake_stream(*args, **kwargs):
            # Record arguments; return minimal valid JSON
            captured_calls.append(kwargs)
            return json.dumps({
                "overall_status": "needs_work",
                "convergence_score": 40,
                "files": [],
                "acceptance_criteria": [],
                "area_scores": [
                    {"area": "requirements_alignment", "score": 40, "notes": ""},
                    {"area": "architecture", "score": 50, "notes": ""},
                    {"area": "code_quality", "score": 45, "notes": ""},
                    {"area": "test_coverage", "score": 30, "notes": ""},
                    {"area": "security", "score": 60, "notes": ""},
                ],
                "summary": "Initial review done",
            })

        runner = CodeReviewerRunner(config=cfg)
        mod_def = ModuleDefinition(
            module_id="full-repo-review",
            name="Full Repository Review",
            description="Test",
            file_paths=[],
        )

        with patch.object(runner, "_stream_from_openai", side_effect=fake_stream):
            result = runner.run_initial_review(
                module_def=mod_def,
                requirements_text="### EPICS\n- [E1] Auth: User authentication",
                working_dir=".",
            )

        assert result.review.overall_status == "needs_work"
        assert len(captured_calls) == 1
        call_kwargs = captured_calls[0]
        # The override_system_prompt should contain the initial review text
        assert "requirements_alignment" in call_kwargs.get("override_system_prompt", "")
        assert _INITIAL_REVIEW_SYSTEM_PROMPT in call_kwargs.get("override_system_prompt", "") or \
               "Requirements alignment" in call_kwargs.get("override_system_prompt", "")
        # The user content should contain the requirements text
        assert "Auth: User authentication" in call_kwargs.get("override_user_content", "")

    def test_run_initial_review_includes_requirements_in_user_content(self):
        from agent_os.code_reviewer.runner import CodeReviewerRunner
        from agent_os.module_maker.schema import ModuleDefinition

        cfg = AgentOSConfig(secrets={"openai_api_key": "sk-test"}, project={"root_path": "."})
        runner = CodeReviewerRunner(config=cfg)
        mod_def = ModuleDefinition(
            module_id="full-repo-review",
            name="Full Repository Review",
            description="",
            file_paths=[],
        )

        requirements_text = "### EPICS\n- [E1] Billing: Subscription management"
        user_content = runner._build_initial_user_content(mod_def, requirements_text, ".")

        assert "Billing: Subscription management" in user_content
        assert "Requirements Document" in user_content


# ---------------------------------------------------------------------------
# 5.1-D  Prompt Generator full-repo-review (Phase 4)
# ---------------------------------------------------------------------------


class TestPromptGeneratorFullRepoReview:
    def test_iteration_1_full_repo_uses_fixes_path(self):
        """Iteration 1 on full-repo-review should call _generate_fixes_prompt, not impl prompt."""
        from agent_os.module_maker.schema import ModuleDefinition
        from agent_os.prompt_generator.runner import PromptGeneratorRunner
        from agent_os.prompt_generator.schema import FileVerdict, ReviewFeedback

        cfg = AgentOSConfig(project={"root_path": "."}, secrets={"openai_api_key": "sk-test"})
        runner = PromptGeneratorRunner(config=cfg)

        mod_def = ModuleDefinition(
            module_id="full-repo-review",
            name="Full Repo",
            file_paths=["main.py"],
        )
        review = ReviewFeedback(
            overall_status="needs_work",
            files=[FileVerdict(file_path="main.py", action="patch", issues=[])],
        )

        fixes_calls: list = []
        impl_calls: list = []

        def fake_fixes(mod, iteration, rev, on_stdout):
            fixes_calls.append(iteration)
            return "Fix: address issues in main.py"

        def fake_impl(spec, mod, iteration, on_stdout):
            impl_calls.append(iteration)
            return "Implement from scratch"

        with patch.object(runner, "_generate_fixes_prompt", side_effect=fake_fixes):
            with patch.object(runner, "_generate_implementation_prompt", side_effect=fake_impl):
                with patch.object(runner.__class__, "_write_prompt", return_value=Path("/tmp/p.md")):
                    runner.run(mod_def, iteration=1, review=review)

        assert len(fixes_calls) == 1, "Should call _generate_fixes_prompt for iter 1 of full-repo-review"
        assert len(impl_calls) == 0, "Should NOT call _generate_implementation_prompt"

    def test_standard_module_iteration_1_uses_impl_path(self):
        """Normal module iteration 1 should still use _generate_implementation_prompt."""
        from agent_os.module_maker.schema import ModuleDefinition
        from agent_os.prompt_generator.runner import PromptGeneratorRunner

        cfg = AgentOSConfig(project={"root_path": "."}, secrets={"openai_api_key": "sk-test"})
        runner = PromptGeneratorRunner(config=cfg)

        mod_def = ModuleDefinition(module_id="auth-module", name="Auth Module", file_paths=[])

        impl_calls: list = []

        def fake_impl(spec, mod, iteration, on_stdout):
            impl_calls.append(iteration)
            return "Implement auth module"

        with patch.object(runner, "_generate_implementation_prompt", side_effect=fake_impl):
            with patch.object(runner.__class__, "_write_prompt", return_value=Path("/tmp/p.md")):
                runner.run(mod_def, iteration=1, review=None)

        assert len(impl_calls) == 1

    def test_standard_module_iteration_2_uses_fixes_path(self):
        """Normal module iteration 2+ should still use _generate_fixes_prompt."""
        from agent_os.module_maker.schema import ModuleDefinition
        from agent_os.prompt_generator.runner import PromptGeneratorRunner
        from agent_os.prompt_generator.schema import FileVerdict, ReviewFeedback

        cfg = AgentOSConfig(project={"root_path": "."}, secrets={"openai_api_key": "sk-test"})
        runner = PromptGeneratorRunner(config=cfg)

        mod_def = ModuleDefinition(module_id="auth-module", name="Auth Module", file_paths=["auth.py"])
        review = ReviewFeedback(
            overall_status="needs_work",
            files=[FileVerdict(file_path="auth.py", action="patch", issues=[])],
        )

        fixes_calls: list = []

        def fake_fixes(mod, iteration, rev, on_stdout):
            fixes_calls.append(iteration)
            return "Fix auth issues"

        with patch.object(runner, "_generate_fixes_prompt", side_effect=fake_fixes):
            with patch.object(runner.__class__, "_write_prompt", return_value=Path("/tmp/p.md")):
                runner.run(mod_def, iteration=2, review=review)

        assert len(fixes_calls) == 1


# ---------------------------------------------------------------------------
# 5.1-E  Pipeline state transitions for github_review mode
# ---------------------------------------------------------------------------


class TestGitHubReviewStateTransitions:
    def test_idle_transitions_to_github_fork_clone(self, handler_ctx: HandlerContext):
        from agent_os.orchestrator.handlers import handle_idle

        handler_ctx.config.pipeline_mode = "github_review"
        # Seed state as IDLE
        state = handler_ctx.state_mgr.state
        state.pipeline_status = PipelineStatus.IDLE
        handler_ctx.db.save_pipeline_state(state)

        handle_idle(handler_ctx)

        new_state = handler_ctx.state_mgr.state
        assert new_state.pipeline_status == PipelineStatus.GITHUB_FORK_CLONE

    def test_idle_standard_mode_transitions_to_loading_requirements(
        self, handler_ctx: HandlerContext
    ):
        from agent_os.orchestrator.handlers import handle_idle

        handler_ctx.config.pipeline_mode = "standard"
        state = handler_ctx.state_mgr.state
        state.pipeline_status = PipelineStatus.IDLE
        handler_ctx.db.save_pipeline_state(state)

        handle_idle(handler_ctx)

        new_state = handler_ctx.state_mgr.state
        assert new_state.pipeline_status == PipelineStatus.LOADING_REQUIREMENTS

    def test_state_transitions_github_review_path(self, handler_ctx: HandlerContext):
        """Verify IDLE→GITHUB_FORK_CLONE→INITIAL_CODE_REVIEW transitions are valid."""
        from agent_os.orchestrator.state import TRANSITIONS

        assert PipelineStatus.GITHUB_FORK_CLONE in TRANSITIONS[PipelineStatus.IDLE]
        assert PipelineStatus.INITIAL_CODE_REVIEW in TRANSITIONS[PipelineStatus.GITHUB_FORK_CLONE]
        assert PipelineStatus.PROMPT_GENERATION in TRANSITIONS[PipelineStatus.INITIAL_CODE_REVIEW]
        assert PipelineStatus.FAILED in TRANSITIONS[PipelineStatus.GITHUB_FORK_CLONE]
        assert PipelineStatus.FAILED in TRANSITIONS[PipelineStatus.INITIAL_CODE_REVIEW]


# ---------------------------------------------------------------------------
# 5.1-F  handle_next_module short-circuit in github_review mode
# ---------------------------------------------------------------------------


class TestNextModuleShortCircuit:
    def test_github_review_mode_skips_to_pipeline_complete(self, handler_ctx: HandlerContext):
        from agent_os.orchestrator.handlers import handle_next_module

        handler_ctx.config.pipeline_mode = "github_review"
        state = handler_ctx.state_mgr.state
        state.pipeline_status = PipelineStatus.NEXT_MODULE
        handler_ctx.db.save_pipeline_state(state)

        handle_next_module(handler_ctx)

        new_state = handler_ctx.state_mgr.state
        assert new_state.pipeline_status == PipelineStatus.PIPELINE_COMPLETE

    def test_standard_mode_next_module_queries_db(self, handler_ctx: HandlerContext):
        from agent_os.orchestrator.handlers import handle_next_module

        handler_ctx.config.pipeline_mode = "standard"
        state = handler_ctx.state_mgr.state
        state.pipeline_status = PipelineStatus.NEXT_MODULE
        handler_ctx.db.save_pipeline_state(state)

        # No modules in DB → should still complete cleanly
        handle_next_module(handler_ctx)

        new_state = handler_ctx.state_mgr.state
        assert new_state.pipeline_status == PipelineStatus.PIPELINE_COMPLETE


# ---------------------------------------------------------------------------
# 5.1-G  Config persistence (Phase 2 / 5.3)
# ---------------------------------------------------------------------------


class TestConfigPersistence:
    def test_github_review_config_defaults(self):
        cfg = AgentOSConfig()
        assert cfg.pipeline_mode == "standard"
        assert cfg.github_review.source_repo_url == ""
        assert cfg.github_review.branch_name == "agent-os-fixes"
        assert cfg.github_review.fork_repo_name == ""
        assert cfg.github_review.requirements_path == ""

    def test_github_review_config_from_dict(self):
        cfg = AgentOSConfig(
            pipeline_mode="github_review",
            github_review={
                "source_repo_url": "https://github.com/owner/repo",
                "requirements_path": "/tmp/req.yaml",
                "fork_repo_name": "my-fork",
                "branch_name": "fixes",
            },
        )
        assert cfg.pipeline_mode == "github_review"
        assert cfg.github_review.source_repo_url == "https://github.com/owner/repo"
        assert cfg.github_review.requirements_path == "/tmp/req.yaml"
        assert cfg.github_review.branch_name == "fixes"

    def test_settings_put_persists_pipeline_mode(self, app_client: TestClient):
        resp = app_client.get("/api/settings")
        assert resp.status_code == 200
        current = resp.json()

        # Toggle to github_review
        update = {**current, "pipeline_mode": "github_review"}
        resp = app_client.put("/api/settings", json=update)
        assert resp.status_code == 200
        assert resp.json()["pipeline_mode"] == "github_review"

        # Toggle back to standard
        update["pipeline_mode"] = "standard"
        resp = app_client.put("/api/settings", json=update)
        assert resp.status_code == 200
        assert resp.json()["pipeline_mode"] == "standard"

    def test_settings_put_persists_github_review_fields(self, app_client: TestClient):
        resp = app_client.get("/api/settings")
        assert resp.status_code == 200
        current = resp.json()

        current["pipeline_mode"] = "github_review"
        current["github_review"] = {
            "source_repo_url": "https://github.com/acme/target-repo",
            "requirements_path": "/tmp/req.yaml",
            "fork_repo_name": "target-repo-fork",
            "branch_name": "agent-fixes",
        }
        resp = app_client.put("/api/settings", json=current)
        assert resp.status_code == 200
        gh = resp.json()["github_review"]
        assert gh["source_repo_url"] == "https://github.com/acme/target-repo"
        assert gh["fork_repo_name"] == "target-repo-fork"
        assert gh["branch_name"] == "agent-fixes"

    def test_start_pipeline_github_review_mode_requires_url(self, app_client: TestClient):
        """POST /api/pipeline/start with github_review mode but no source_repo_url → 422."""
        resp = app_client.post(
            "/api/pipeline/start",
            json={"pipeline_mode": "github_review", "source_repo_url": ""},
        )
        assert resp.status_code == 422

    def test_start_pipeline_standard_mode_ok(self, app_client: TestClient):
        resp = app_client.post(
            "/api/pipeline/start",
            json={"pipeline_mode": "standard"},
        )
        # 200 OK or 409 Conflict (already running) — not a validation error
        assert resp.status_code in (200, 409)
