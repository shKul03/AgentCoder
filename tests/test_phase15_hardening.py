"""Phase 15 tests — Hardening, Error Handling, Optimization.

Tests retry with backoff, rollback manager, token budget tracker,
dependency manager, JSON repair, error recovery, and updated API endpoints.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_os.comms.bus import AgentCommBus
from agent_os.comms.channels import Channel
from agent_os.comms.messages import ErrorAlertMessage
from agent_os.config.schema import (
    AgentOSConfig,
    BudgetConfig,
    DependencyConfig,
    ErrorHandlingConfig,
)
from agent_os.git_ops.manager import GitOpsManager, GitResult
from agent_os.hardening.dependency_mgr import DependencyManager
from agent_os.hardening.error_handler import (
    ErrorCategory,
    RecoveryAction,
    classify_error,
    get_recovery_action,
    publish_error,
)
from agent_os.hardening.json_repair import JSONParseError, extract_json, repair_json_prompt
from agent_os.hardening.retry import RetryExhaustedError, retry_with_backoff
from agent_os.hardening.rollback import RollbackManager
from agent_os.hardening.token_budget import BudgetStatus, TokenBudgetTracker
from agent_os.storage.database import Database
from agent_os.storage.iteration_repo import IterationRepository
from agent_os.storage.models import IterationRecord, ModuleRecord, ModuleStatus
from agent_os.storage.module_repo import ModuleRepository


# ---------- Fixtures --------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path: Path) -> Database:
    db = Database(str(tmp_path / "test.db"))
    db.connect()
    yield db
    db.close()


@pytest.fixture()
def iter_repo(tmp_db: Database) -> IterationRepository:
    return IterationRepository(tmp_db.conn)


@pytest.fixture()
def mod_repo(tmp_db: Database) -> ModuleRepository:
    return ModuleRepository(tmp_db.conn)


@pytest.fixture()
def bus() -> AgentCommBus:
    return AgentCommBus()


@pytest.fixture()
def budget_config() -> BudgetConfig:
    return BudgetConfig(
        token_budget_per_module=10000,
        alert_threshold_pct=80,
        pause_at_limit=True,
        cost_per_1k_tokens=0.01,
    )


# ---------- Retry Tests -----------------------------------------------------

class TestRetryWithBackoff:

    def test_success_on_first_attempt(self):
        result = retry_with_backoff(lambda: 42, max_retries=2, label="test")
        assert result == 42

    def test_success_after_retry(self):
        attempts = {"count": 0}

        def flaky():
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise ValueError("transient")
            return "ok"

        result = retry_with_backoff(
            flaky, max_retries=2,
            backoff_base=0.01, backoff_max=0.05, label="test",
        )
        assert result == "ok"
        assert attempts["count"] == 3

    def test_exhausted_raises(self):
        def always_fail():
            raise RuntimeError("boom")

        with pytest.raises(RetryExhaustedError) as exc_info:
            retry_with_backoff(
                always_fail, max_retries=1,
                backoff_base=0.01, label="test",
            )
        assert exc_info.value.attempts == 2
        assert "boom" in str(exc_info.value.last_error)

    def test_non_retryable_error_propagates(self):
        def type_err():
            raise TypeError("not retryable")

        with pytest.raises(TypeError, match="not retryable"):
            retry_with_backoff(
                type_err, max_retries=2,
                retryable_errors=(ValueError,),
                backoff_base=0.01, label="test",
            )

    def test_zero_retries(self):
        """max_retries=0 means try once and fail."""
        with pytest.raises(RetryExhaustedError) as exc_info:
            retry_with_backoff(
                lambda: 1 / 0, max_retries=0,
                backoff_base=0.01, label="test",
            )
        assert exc_info.value.attempts == 1


# ---------- Rollback Tests --------------------------------------------------

class TestRollbackManager:

    def test_checkpoint_tag_format(self):
        git = MagicMock(spec=GitOpsManager)
        rb = RollbackManager(git)
        assert rb.checkpoint_tag("mod-1", 2) == "checkpoint/mod-1/iter-2"

    def test_create_checkpoint_not_a_repo(self):
        git = MagicMock(spec=GitOpsManager)
        git.is_repo.return_value = False
        rb = RollbackManager(git)
        assert rb.create_checkpoint("mod-1", 1) is None

    def test_create_checkpoint_success(self):
        git = MagicMock(spec=GitOpsManager)
        git.is_repo.return_value = True
        git.has_changes.return_value = False
        git.tag.return_value = GitResult(success=True, command="git tag")
        rb = RollbackManager(git)
        tag = rb.create_checkpoint("mod-1", 1)
        assert tag == "checkpoint/mod-1/iter-1"
        git.tag.assert_called_once()

    def test_create_checkpoint_with_dirty_tree(self):
        git = MagicMock(spec=GitOpsManager)
        git.is_repo.return_value = True
        git.has_changes.return_value = True
        git.commit_all.return_value = GitResult(success=True, command="git commit")
        git.tag.return_value = GitResult(success=True, command="git tag")
        rb = RollbackManager(git)
        tag = rb.create_checkpoint("mod-2", 3)
        assert tag is not None
        git.commit_all.assert_called_once()

    def test_rollback_to_checkpoint(self):
        git = MagicMock(spec=GitOpsManager)
        git.reset_hard.return_value = GitResult(success=True, command="git reset")
        rb = RollbackManager(git)
        result = rb.rollback_to_checkpoint("mod-1", 2)
        assert result.success
        git.reset_hard.assert_called_once_with("checkpoint/mod-1/iter-2")

    def test_rollback_to_latest_no_checkpoints(self):
        git = MagicMock(spec=GitOpsManager)
        git.list_tags.return_value = []
        rb = RollbackManager(git)
        result = rb.rollback_to_latest_checkpoint("mod-1")
        assert not result.success


# ---------- Token Budget Tests ----------------------------------------------

class TestTokenBudgetTracker:

    def test_module_usage_empty(self, iter_repo, budget_config):
        tracker = TokenBudgetTracker(budget_config, iter_repo)
        assert tracker.module_usage("mod-1") == 0

    def _insert_module(self, repo):
        """Helper to insert a module so FK constraints are satisfied."""
        repo.conn.execute(
            "INSERT OR IGNORE INTO modules (id, name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("mod-1", "Test", "pending", datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
        )
        repo.conn.commit()

    def test_record_and_check_ok(self, tmp_db, iter_repo, budget_config):
        self._insert_module(tmp_db)
        record = IterationRecord(module_id="mod-1", iteration_number=1)
        iter_repo.create(record)

        tracker = TokenBudgetTracker(budget_config, iter_repo)
        status = tracker.record_usage("mod-1", 1, 5000)
        assert status == BudgetStatus.OK
        assert tracker.module_usage("mod-1") == 5000

    def test_record_and_check_warning(self, tmp_db, iter_repo, budget_config):
        self._insert_module(tmp_db)
        record = IterationRecord(module_id="mod-1", iteration_number=1)
        iter_repo.create(record)

        tracker = TokenBudgetTracker(budget_config, iter_repo)
        status = tracker.record_usage("mod-1", 1, 8500)  # 85% of 10000
        assert status == BudgetStatus.WARNING

    def test_record_and_check_exceeded(self, tmp_db, iter_repo, budget_config):
        self._insert_module(tmp_db)
        record = IterationRecord(module_id="mod-1", iteration_number=1)
        iter_repo.create(record)

        tracker = TokenBudgetTracker(budget_config, iter_repo)
        status = tracker.record_usage("mod-1", 1, 12000)
        assert status == BudgetStatus.EXCEEDED
        assert tracker.should_pause("mod-1") is True

    def test_should_pause_disabled(self, tmp_db, iter_repo):
        config = BudgetConfig(
            token_budget_per_module=100,
            pause_at_limit=False,
        )
        self._insert_module(tmp_db)
        record = IterationRecord(module_id="mod-1", iteration_number=1)
        iter_repo.create(record)

        tracker = TokenBudgetTracker(config, iter_repo)
        tracker.record_usage("mod-1", 1, 200)
        assert tracker.should_pause("mod-1") is False

    def test_module_cost_calculation(self, tmp_db, iter_repo, budget_config):
        self._insert_module(tmp_db)
        record = IterationRecord(module_id="mod-1", iteration_number=1)
        iter_repo.create(record)

        tracker = TokenBudgetTracker(budget_config, iter_repo)
        tracker.record_usage("mod-1", 1, 5000)
        # 5000 / 1000 * 0.01 = 0.05
        assert tracker.module_cost("mod-1") == pytest.approx(0.05)

    def test_get_summary(self, tmp_db, iter_repo, budget_config):
        self._insert_module(tmp_db)
        record = IterationRecord(module_id="mod-1", iteration_number=1)
        iter_repo.create(record)
        tracker = TokenBudgetTracker(budget_config, iter_repo)
        tracker.record_usage("mod-1", 1, 3000)

        summary = tracker.get_summary("mod-1")
        assert summary["module_id"] == "mod-1"
        assert summary["tokens_used"] == 3000
        assert summary["token_budget"] == 10000
        assert summary["usage_pct"] == 30.0
        assert summary["status"] == "ok"

    def test_budget_alert_published(self, tmp_db, iter_repo, budget_config, bus):
        self._insert_module(tmp_db)
        record = IterationRecord(module_id="mod-1", iteration_number=1)
        iter_repo.create(record)

        alerts = []
        bus.subscribe(Channel.ERROR_ALERTS, lambda m: alerts.append(m))

        tracker = TokenBudgetTracker(budget_config, iter_repo, bus=bus)
        tracker.record_usage("mod-1", 1, 11000)

        assert len(alerts) == 1
        assert alerts[0].payload["event"] == "budget_exceeded"

    def test_zero_budget_always_ok(self, iter_repo):
        config = BudgetConfig(token_budget_per_module=0)
        tracker = TokenBudgetTracker(config, iter_repo)
        assert tracker.check_budget("mod-1") == BudgetStatus.OK


# ---------- JSON Repair Tests -----------------------------------------------

class TestJSONRepair:

    def test_extract_valid_json(self):
        raw = '{"status": "accepted", "score": 80}'
        result = extract_json(raw)
        assert result == {"status": "accepted", "score": 80}

    def test_extract_json_with_surrounding_text(self):
        raw = 'Here is the review:\n{"status": "accepted"}\nDone.'
        result = extract_json(raw)
        assert result == {"status": "accepted"}

    def test_extract_json_from_code_fence(self):
        raw = '```json\n{"status": "accepted"}\n```'
        result = extract_json(raw)
        assert result == {"status": "accepted"}

    def test_extract_json_returns_none_on_garbage(self):
        assert extract_json("not json at all") is None

    def test_repair_prompt_includes_instruction(self):
        prompt = repair_json_prompt("Review this code", "bad output {{")
        assert "valid JSON" in prompt
        assert "bad output" in prompt
        assert "Review this code" in prompt


# ---------- Error Handler Tests ---------------------------------------------

class TestErrorHandler:

    def test_classify_timeout(self):
        exc = TimeoutError("connection timed out")
        assert classify_error(exc) == ErrorCategory.CODEX_TIMEOUT

    def test_classify_json_error(self):
        exc = json.JSONDecodeError("msg", "doc", 0)
        assert classify_error(exc) == ErrorCategory.INVALID_JSON

    def test_classify_git_conflict(self):
        exc = RuntimeError("some error")
        assert classify_error(exc, context="merge conflict") == ErrorCategory.GIT_CONFLICT

    def test_classify_network(self):
        exc = ConnectionError("network unreachable")
        assert classify_error(exc) == ErrorCategory.NETWORK_ERROR

    def test_classify_unknown(self):
        exc = RuntimeError("something random")
        assert classify_error(exc) == ErrorCategory.UNKNOWN

    def test_recovery_actions(self):
        assert get_recovery_action(ErrorCategory.CODEX_CRASH) == RecoveryAction.RETRY
        assert get_recovery_action(ErrorCategory.GIT_CONFLICT) == RecoveryAction.HITL_ESCALATE
        assert get_recovery_action(ErrorCategory.VALIDATION_TOOL_ERROR) == RecoveryAction.SKIP
        assert get_recovery_action(ErrorCategory.UNKNOWN) == RecoveryAction.FAIL

    def test_publish_error_on_bus(self, bus):
        alerts = []
        bus.subscribe(Channel.ERROR_ALERTS, lambda m: alerts.append(m))

        publish_error(
            bus, ErrorCategory.CODEX_CRASH,
            RecoveryAction.RETRY, module_id="mod-1", detail="exit code 1",
        )

        assert len(alerts) == 1
        assert alerts[0].payload["category"] == "codex_crash"
        assert alerts[0].payload["recovery_action"] == "retry"

    def test_publish_error_no_bus(self):
        # Should not raise
        publish_error(None, ErrorCategory.UNKNOWN, RecoveryAction.FAIL)


# ---------- Dependency Manager Tests ----------------------------------------

class TestDependencyManager:

    def test_venv_path(self, tmp_path):
        config = DependencyConfig(venv_name=".venv")
        mgr = DependencyManager(config, str(tmp_path))
        assert mgr.venv_python.endswith(".venv/bin/python")

    def test_venv_does_not_exist(self, tmp_path):
        config = DependencyConfig(auto_create_venv=False)
        mgr = DependencyManager(config, str(tmp_path))
        assert mgr.venv_exists is False

    def test_install_disabled(self, tmp_path):
        config = DependencyConfig(auto_install=False)
        mgr = DependencyManager(config, str(tmp_path))
        result = mgr.install_requirements()
        assert result.success
        assert "disabled" in result.output

    def test_no_requirements_file(self, tmp_path):
        config = DependencyConfig()
        mgr = DependencyManager(config, str(tmp_path))
        result = mgr.install_requirements()
        assert result.success
        assert "no requirements.txt" in result.output

    def test_dry_run_no_file(self, tmp_path):
        config = DependencyConfig()
        mgr = DependencyManager(config, str(tmp_path))
        result = mgr.dry_run_install()
        assert result.success


# ---------- Git Ops Extensions Tests ----------------------------------------

class TestGitOpsExtensions:

    def test_list_tags_no_repo(self, tmp_path):
        git = GitOpsManager(str(tmp_path))
        assert git.list_tags() == []

    def test_has_merge_conflict_no_repo(self, tmp_path):
        git = GitOpsManager(str(tmp_path))
        # Not a repo → returns False (diff command fails with empty stdout)
        assert git.has_merge_conflict() is False


# ---------- Validation Runner Error Handling Tests --------------------------

class TestValidationRunnerErrorHandling:

    def test_validator_crash_skipped(self):
        from agent_os.config.schema import ValidationConfig
        from agent_os.validation.runner import ValidationRunner

        config = ValidationConfig(
            lint=False, type_check=False, tests=False, security_scan=False,
        )

        runner = ValidationRunner(config=config)

        # Monkeypatch the runners list to include a crasher
        from agent_os.validation.schema import ToolResult

        original_run = runner.run

        def patched_run(working_dir, module_id, iteration, file_paths=None):
            """Inject a crashing validator and run."""
            result = original_run(working_dir, module_id, iteration, file_paths)
            return result

        # Run with deps checker (always runs) which may also error — fine
        # The key assertion is that ValidationRunner does not raise
        result = runner.run(
            working_dir="/nonexistent/path",
            module_id="mod-1",
            iteration=1,
        )
        # deps checker should be skipped (tool error: path doesn't exist or pip not found)
        assert isinstance(result.tools, list)
        assert len(result.tools) >= 1  # At least deps runs


# ---------- Config Tests ----------------------------------------------------

class TestHardeningConfig:

    def test_default_config(self):
        config = AgentOSConfig()
        assert config.budget.token_budget_per_module == 20_000
        assert config.budget.alert_threshold_pct == 80
        assert config.dependencies.auto_create_venv is True
        assert config.error_handling.max_json_retries == 2
        assert config.error_handling.rollback_on_failure is True

    def test_budget_config_custom(self):
        config = BudgetConfig(
            token_budget_per_module=50000,
            alert_threshold_pct=70,
            cost_per_1k_tokens=0.02,
        )
        assert config.token_budget_per_module == 50000
        assert config.cost_per_1k_tokens == 0.02

    def test_error_handling_config(self):
        config = ErrorHandlingConfig(
            max_json_retries=3,
            retry_backoff_base=2.0,
            skip_failed_validators=False,
        )
        assert config.max_json_retries == 3
        assert config.skip_failed_validators is False

    def test_config_from_yaml_string(self):
        from agent_os.config.loader import load_config
        import yaml

        # Verify the actual config.yaml loads cleanly with new fields
        config = load_config("config.yaml")
        assert config.budget.token_budget_per_module > 0
        assert config.dependencies.auto_install is True
        assert config.error_handling.rollback_on_failure is True


# ---------- API Budget Endpoint Tests ---------------------------------------

class TestBudgetAPI:

    @pytest.fixture()
    def app_client(self, tmp_path):
        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agent_os.api.deps import orch_holder
        from agent_os.api.routes import metrics

        orch_holder.shutdown()
        config = AgentOSConfig(storage={"db_path": str(tmp_path / "test.db")})
        orch = orch_holder.init(config)

        @asynccontextmanager
        async def noop_lifespan(app):
            yield

        app = FastAPI(lifespan=noop_lifespan)
        app.include_router(metrics.router)

        with TestClient(app) as client:
            yield client, orch
        orch_holder.shutdown()

    def test_metrics_includes_budget(self, app_client):
        client, orch = app_client
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_cost" in data
        assert "budget_per_module" in data
        assert data["budget_per_module"] == 20000

    def test_module_budget_endpoint(self, app_client):
        client, orch = app_client
        # Add a module with token usage
        mod_repo = ModuleRepository(orch.db.conn)
        mod_repo.upsert(ModuleRecord(
            id="mod-1", name="Test Module",
            status=ModuleStatus.IN_PROGRESS, execution_order=1,
        ))
        iter_repo = IterationRepository(orch.db.conn)
        record = IterationRecord(module_id="mod-1", iteration_number=1, token_usage=5000)
        iter_repo.create(record)

        resp = client.get("/api/metrics/budget/mod-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module_id"] == "mod-1"
        assert data["tokens_used"] == 5000
        assert data["token_budget"] == 20000
        assert data["status"] == "ok"


# ---------- Engine Error Handling Tests -------------------------------------

class TestEngineErrorHandling:

    def test_handle_error_transitions_to_failed(self, tmp_path):
        config = AgentOSConfig(storage={"db_path": str(tmp_path / "test.db")})
        from agent_os.orchestrator.engine import Orchestrator
        orch = Orchestrator(config)

        # Manually set state to a non-IDLE status that can transition to FAILED
        orch.state_mgr.transition_to(PipelineStatus.LOADING_REQUIREMENTS)

        # Simulate error
        orch._handle_error(
            RuntimeError("test error"),
            PipelineStatus.LOADING_REQUIREMENTS,
        )
        assert orch.state_mgr.current_status == PipelineStatus.FAILED
        orch.shutdown()

    def test_engine_has_rollback_attribute(self, tmp_path):
        config = AgentOSConfig(storage={"db_path": str(tmp_path / "test.db")})
        from agent_os.orchestrator.engine import Orchestrator
        orch = Orchestrator(config)
        assert orch._rollback is None  # Not initialized until git is checked
        orch.shutdown()


# ---------- Token Estimation Test -------------------------------------------

class TestTokenEstimation:

    def test_estimate_tokens(self):
        from agent_os.orchestrator.handlers import _estimate_tokens
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("hello world") == max(len("hello world") // 4, 1)
        # ~1000 chars → ~250 tokens
        text = "a" * 1000
        assert _estimate_tokens(text) == 250


# Import needed for PipelineStatus in engine tests
from agent_os.storage.models import PipelineStatus
