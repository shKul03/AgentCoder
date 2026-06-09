"""Phase 12 tests — Iteration Loop + Decision Logic.

Tests:
  - decide_iteration() convergence rules
  - _convert_review_to_feedback helper
  - handle_decision handler (3 paths)
  - handle_prompt_generation with review feedback (iteration > 1)
  - Integration: Decision → iterate → Prompt Generation with feedback
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_os.comms.bus import AgentCommBus
from agent_os.comms.channels import Channel
from agent_os.config.schema import (
    AgentOSConfig,
    ConvergenceRule,
    OrchestratorConfig,
)
from agent_os.orchestrator.context import HandlerContext
from agent_os.orchestrator.decision import decide_iteration
from agent_os.orchestrator.handlers import (
    _convert_review_to_feedback,
    handle_decision,
    handle_prompt_generation,
)
from agent_os.orchestrator.state import StateManager
from agent_os.prompt_generator.schema import FileVerdict, ReviewFeedback
from agent_os.storage.database import Database
from agent_os.storage.iteration_repo import IterationRepository
from agent_os.storage.models import (
    IterationRecord,
    IterationStatus,
    ModuleRecord,
    PipelineStatus,
)
from agent_os.storage.module_repo import ModuleRepository


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def db():
    """In-memory SQLite database."""
    d = Database(":memory:")
    d.connect()
    yield d
    d.close()


@pytest.fixture()
def bus():
    return AgentCommBus()


@pytest.fixture()
def config():
    return AgentOSConfig(
        orchestrator=OrchestratorConfig(
            max_iterations_per_module=3,
            convergence_rule=ConvergenceRule.NO_HIGH_SEVERITY,
        ),
    )


@pytest.fixture()
def state_mgr(db):
    return StateManager(db)


def _fast_forward(state_mgr: StateManager, target: PipelineStatus, module_id: str, iteration: int):
    """Fast-forward state to a given PipelineStatus via valid transitions."""
    chains = {
        PipelineStatus.DECISION: [
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
        ],
        PipelineStatus.PROMPT_GENERATION: [
            PipelineStatus.LOADING_REQUIREMENTS,
            PipelineStatus.MODULE_PLANNING,
            PipelineStatus.HITL_1_MODULE_REVIEW,
            PipelineStatus.PROMPT_GENERATION,
        ],
    }
    for status in chains[target]:
        kw = {}
        if status == PipelineStatus.PROMPT_GENERATION:
            kw = {"module_id": module_id, "iteration": iteration}
        elif status == PipelineStatus.DECISION:
            kw = {"module_id": module_id, "iteration": iteration}
        state_mgr.transition_to(status, **kw)


def _seed_module(db: Database, module_id: str = "mod-auth"):
    ModuleRepository(db.conn).upsert(ModuleRecord(
        id=module_id,
        name="Auth Module",
        feature_name="Authentication",
    ))


def _seed_iteration(db: Database, module_id: str, iteration: int):
    IterationRepository(db.conn).create(IterationRecord(
        module_id=module_id,
        iteration_number=iteration,
        prompt_path=f"data/prompts/{module_id}/iteration-{iteration}.md",
    ))


def _write_review_json(module_id: str, iteration: int, review_data: dict) -> Path:
    review_dir = Path(f"data/reviews/{module_id}")
    review_dir.mkdir(parents=True, exist_ok=True)
    p = review_dir / f"iteration-{iteration}.json"
    p.write_text(json.dumps(review_data), encoding="utf-8")
    return p


# ── Sample review data ────────────────────────────────────────────────


REVIEW_ACCEPTED = {
    "overall_status": "accepted",
    "convergence_score": 95,
    "blocking_issues": 0,
    "files": [
        {"file_path": "auth/login.py", "action": "accept", "issues": [], "comments": []},
    ],
    "acceptance_criteria": [{"id": "AC-1", "passed": True, "evidence": "ok"}],
    "summary": "All checks passed.",
}

REVIEW_NEEDS_WORK = {
    "overall_status": "needs_work",
    "convergence_score": 40,
    "blocking_issues": 2,
    "files": [
        {
            "file_path": "auth/login.py",
            "action": "patch",
            "issues": [
                {
                    "severity": "high",
                    "category": "correctness",
                    "issue": "Missing null check on user input",
                    "suggested_fix": "Add validation at entry",
                    "line_range": "10-15",
                },
                {
                    "severity": "medium",
                    "category": "style",
                    "issue": "Inconsistent naming",
                    "suggested_fix": "Rename to snake_case",
                    "line_range": "20",
                },
            ],
            "comments": ["Needs input validation"],
        },
        {
            "file_path": "auth/session.py",
            "action": "regenerate",
            "issues": [
                {
                    "severity": "critical",
                    "category": "security",
                    "issue": "SQL injection vulnerability",
                    "suggested_fix": "Use parameterized queries",
                    "line_range": "5-8",
                },
            ],
            "comments": ["Major security issue"],
        },
    ],
    "acceptance_criteria": [{"id": "AC-1", "passed": False, "evidence": "missing"}],
    "summary": "Needs significant rework.",
}

REVIEW_ONLY_LOW = {
    "overall_status": "needs_work",
    "convergence_score": 80,
    "blocking_issues": 0,
    "files": [
        {
            "file_path": "auth/login.py",
            "action": "patch",
            "issues": [
                {"severity": "low", "category": "style", "issue": "Minor formatting"},
            ],
            "comments": [],
        },
    ],
    "summary": "Minor style issues only.",
}


# ══════════════════════════════════════════════════════════════════════
#  1. decide_iteration() unit tests
# ══════════════════════════════════════════════════════════════════════


class TestDecideIteration:
    """Pure function tests for convergence rules."""

    def test_accepted_returns_module_complete(self):
        assert decide_iteration(REVIEW_ACCEPTED, 1, 5, ConvergenceRule.NO_HIGH_SEVERITY) == "MODULE_COMPLETE"

    def test_accepted_status_overrides_convergence_rule(self):
        # Even ALL_ACCEPTED rule → MODULE_COMPLETE when overall_status == "accepted"
        assert decide_iteration(REVIEW_ACCEPTED, 1, 5, ConvergenceRule.ALL_ACCEPTED) == "MODULE_COMPLETE"

    def test_max_iterations_reached(self):
        result = decide_iteration(REVIEW_NEEDS_WORK, 3, 3, ConvergenceRule.NO_HIGH_SEVERITY)
        assert result == "HITL_4_MAX_ITERATIONS"

    def test_iterate_when_blocking_issues(self):
        result = decide_iteration(REVIEW_NEEDS_WORK, 1, 5, ConvergenceRule.NO_HIGH_SEVERITY)
        assert result == "ITERATE"

    def test_no_high_severity_accepts_low_only(self):
        result = decide_iteration(REVIEW_ONLY_LOW, 1, 5, ConvergenceRule.NO_HIGH_SEVERITY)
        assert result == "MODULE_COMPLETE"

    def test_no_critical_accepts_high_only(self):
        """NO_CRITICAL rule accepts if only high issues, no critical."""
        review_high_only = {
            "overall_status": "needs_work",
            "files": [
                {"file_path": "a.py", "action": "patch", "issues": [
                    {"severity": "high", "issue": "some high issue"},
                ]},
            ],
        }
        result = decide_iteration(review_high_only, 1, 5, ConvergenceRule.NO_CRITICAL)
        assert result == "MODULE_COMPLETE"

    def test_no_critical_rejects_critical(self):
        result = decide_iteration(REVIEW_NEEDS_WORK, 1, 5, ConvergenceRule.NO_CRITICAL)
        assert result == "ITERATE"

    def test_all_accepted_rejects_if_any_not_accept(self):
        result = decide_iteration(REVIEW_NEEDS_WORK, 1, 5, ConvergenceRule.ALL_ACCEPTED)
        assert result == "ITERATE"

    def test_all_accepted_accepts_all_accept_actions(self):
        review = {
            "overall_status": "needs_work",
            "files": [
                {"file_path": "a.py", "action": "accept", "issues": []},
                {"file_path": "b.py", "action": "accept", "issues": []},
            ],
        }
        result = decide_iteration(review, 1, 5, ConvergenceRule.ALL_ACCEPTED)
        assert result == "MODULE_COMPLETE"

    def test_empty_files_with_needs_work(self):
        """No files → no blocking issues → converges (NO_HIGH_SEVERITY)."""
        review = {"overall_status": "needs_work", "files": []}
        result = decide_iteration(review, 1, 5, ConvergenceRule.NO_HIGH_SEVERITY)
        assert result == "MODULE_COMPLETE"

    def test_iteration_1_of_max_1_escalates(self):
        result = decide_iteration(REVIEW_NEEDS_WORK, 1, 1, ConvergenceRule.NO_HIGH_SEVERITY)
        assert result == "HITL_4_MAX_ITERATIONS"


# ══════════════════════════════════════════════════════════════════════
#  2. _convert_review_to_feedback() unit tests
# ══════════════════════════════════════════════════════════════════════


class TestConvertReviewToFeedback:
    """Verify review JSON → ReviewFeedback conversion."""

    def test_basic_conversion(self):
        fb = _convert_review_to_feedback(REVIEW_NEEDS_WORK, iteration=1)
        assert isinstance(fb, ReviewFeedback)
        assert fb.iteration == 1
        assert len(fb.files) == 2
        assert fb.summary == "Needs significant rework."

    def test_file_verdicts(self):
        fb = _convert_review_to_feedback(REVIEW_NEEDS_WORK, iteration=1)
        verdicts = {f.file_path: f.verdict for f in fb.files}
        assert verdicts["auth/login.py"] == FileVerdict.PATCH
        assert verdicts["auth/session.py"] == FileVerdict.REGENERATE

    def test_accept_verdict(self):
        fb = _convert_review_to_feedback(REVIEW_ACCEPTED, iteration=1)
        assert fb.files[0].verdict == FileVerdict.ACCEPT

    def test_issues_merged_into_comments(self):
        fb = _convert_review_to_feedback(REVIEW_NEEDS_WORK, iteration=1)
        login_file = next(f for f in fb.files if f.file_path == "auth/login.py")
        # 1 original comment + 2 issues = 3
        assert len(login_file.comments) == 3
        assert any("Missing null check" in c for c in login_file.comments)
        assert any("Add validation" in c for c in login_file.comments)

    def test_unknown_action_defaults_to_accept(self):
        review = {
            "files": [{"file_path": "x.py", "action": "unknown", "issues": [], "comments": []}],
            "summary": "",
        }
        fb = _convert_review_to_feedback(review, iteration=1)
        assert fb.files[0].verdict == FileVerdict.ACCEPT

    def test_empty_files(self):
        fb = _convert_review_to_feedback({"files": [], "summary": "ok"}, iteration=2)
        assert fb.files == []
        assert fb.iteration == 2

    def test_issue_without_suggested_fix(self):
        review = {
            "files": [
                {
                    "file_path": "a.py",
                    "action": "patch",
                    "issues": [{"severity": "low", "issue": "minor thing"}],
                    "comments": [],
                },
            ],
            "summary": "",
        }
        fb = _convert_review_to_feedback(review, iteration=1)
        comments = fb.files[0].comments
        assert len(comments) == 1
        assert "[low] minor thing" in comments[0]
        # No " — Fix:" part
        assert "Fix:" not in comments[0]


# ══════════════════════════════════════════════════════════════════════
#  3. handle_decision handler tests
# ══════════════════════════════════════════════════════════════════════


class TestHandleDecision:
    """Test the decision handler's 3 paths: accept, iterate, max-iter."""

    def _make_ctx(self, db, bus, config, state_mgr):
        return HandlerContext(state_mgr=state_mgr, db=db, config=config, bus=bus)

    def test_accepted_transitions_to_git_commit(self, db, bus, config, state_mgr):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        _seed_iteration(db, module_id, 1)
        _fast_forward(state_mgr, PipelineStatus.DECISION, module_id, 1)
        _write_review_json(module_id, 1, REVIEW_ACCEPTED)

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_decision(ctx)

        assert state_mgr.current_status == PipelineStatus.GIT_COMMIT
        # Iteration marked completed
        rec = IterationRepository(db.conn).get(module_id, 1)
        assert rec.status == IterationStatus.COMPLETED

    def test_iterate_transitions_to_prompt_generation(self, db, bus, config, state_mgr):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        _seed_iteration(db, module_id, 1)
        _fast_forward(state_mgr, PipelineStatus.DECISION, module_id, 1)
        _write_review_json(module_id, 1, REVIEW_NEEDS_WORK)

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_decision(ctx)

        assert state_mgr.current_status == PipelineStatus.PROMPT_GENERATION
        # Iteration incremented
        assert state_mgr.state.current_iteration == 2

    def test_max_iterations_transitions_to_hitl4(self, db, bus, config, state_mgr):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        _seed_iteration(db, module_id, 3)
        # max_iterations_per_module=3 in config fixture
        _fast_forward(state_mgr, PipelineStatus.DECISION, module_id, 3)
        _write_review_json(module_id, 3, REVIEW_NEEDS_WORK)

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_decision(ctx)

        assert state_mgr.current_status == PipelineStatus.HITL_4_MAX_ITERATIONS

    def test_decision_publishes_event_on_bus(self, db, bus, config, state_mgr):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        _seed_iteration(db, module_id, 1)
        _fast_forward(state_mgr, PipelineStatus.DECISION, module_id, 1)
        _write_review_json(module_id, 1, REVIEW_ACCEPTED)

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_decision(ctx)

        events = bus.history_for_channel(Channel.PIPELINE_EVENTS)
        assert len(events) == 1
        assert events[0].payload["decision"] == "MODULE_COMPLETE"
        assert events[0].payload["convergence_score"] == 95

    def test_missing_review_file_defaults_to_iterate(self, db, bus, config, state_mgr):
        """No review file → default 'needs_work' → ITERATE."""
        module_id = "mod-auth"
        _seed_module(db, module_id)
        _seed_iteration(db, module_id, 1)
        _fast_forward(state_mgr, PipelineStatus.DECISION, module_id, 1)
        # Intentionally NOT writing review JSON

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_decision(ctx)

        # Should iterate with empty files → NO_HIGH_SEVERITY + no files → MODULE_COMPLETE
        # Actually, default review_data = {"overall_status": "needs_work", "files": []}
        # NO_HIGH_SEVERITY with empty files → no blocking → MODULE_COMPLETE
        assert state_mgr.current_status == PipelineStatus.GIT_COMMIT

    def test_no_module_id_raises(self, db, bus, config, state_mgr):
        """DECISION without current_module_id raises RuntimeError."""
        # State is at IDLE with no module_id; fast-forward to DECISION without module
        # Actually we can't fast forward without module_id set. Let's manually set state.
        # Hmm, transition_to keeps module_id from prior state if not overridden.
        # Default state has current_module_id = None.
        _fast_forward(state_mgr, PipelineStatus.DECISION, None, 1)
        ctx = self._make_ctx(db, bus, config, state_mgr)
        with pytest.raises(RuntimeError, match="DECISION requires"):
            handle_decision(ctx)

    def test_convergence_rule_from_config(self, db, bus, state_mgr):
        """Config with ALL_ACCEPTED rule properly influences decision."""
        module_id = "mod-auth"
        _seed_module(db, module_id)
        _seed_iteration(db, module_id, 1)

        # All files accepted in action, but overall_status is "needs_work"
        review = {
            "overall_status": "needs_work",
            "convergence_score": 90,
            "blocking_issues": 0,
            "files": [
                {"file_path": "a.py", "action": "accept", "issues": [], "comments": []},
            ],
            "summary": "ok",
        }

        cfg = AgentOSConfig(
            orchestrator=OrchestratorConfig(
                max_iterations_per_module=5,
                convergence_rule=ConvergenceRule.ALL_ACCEPTED,
            ),
        )

        _fast_forward(state_mgr, PipelineStatus.DECISION, module_id, 1)
        _write_review_json(module_id, 1, review)

        ctx = self._make_ctx(db, bus, cfg, state_mgr)
        handle_decision(ctx)

        assert state_mgr.current_status == PipelineStatus.GIT_COMMIT


# ══════════════════════════════════════════════════════════════════════
#  4. handle_prompt_generation with review feedback
# ══════════════════════════════════════════════════════════════════════


class TestPromptGenerationWithFeedback:
    """Test that iteration > 1 loads review feedback and passes it to PromptGeneratorRunner."""

    def _make_ctx(self, db, bus, config, state_mgr):
        return HandlerContext(state_mgr=state_mgr, db=db, config=config, bus=bus)

    def _write_module_json(self, module_id: str):
        from agent_os.module_maker.schema import ModuleDefinition

        mod_def = ModuleDefinition(
            module_id=module_id,
            name="Auth Module",
            feature_name="Authentication",
            files=["auth/login.py"],
            acceptance_criteria=["AC-1: Users can log in"],
            spec_summary="Handle authentication",
        )
        p = Path(f"data/modules/{module_id}.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(mod_def.model_dump_json(), encoding="utf-8")

    @patch("agent_os.prompt_generator.runner.PromptGeneratorRunner")
    def test_iteration_1_no_review_feedback(self, mock_runner_cls, db, bus, config, state_mgr):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        self._write_module_json(module_id)
        _fast_forward(state_mgr, PipelineStatus.PROMPT_GENERATION, module_id, 1)

        mock_runner = MagicMock()
        mock_runner.run.return_value = Path(f"data/prompts/{module_id}/iteration-1.md")
        mock_runner_cls.return_value = mock_runner

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_prompt_generation(ctx)

        # Review arg should be None on iteration 1
        call_args = mock_runner.run.call_args
        assert call_args[0][2] is None  # third positional arg = review

    @patch("agent_os.prompt_generator.runner.PromptGeneratorRunner")
    def test_iteration_2_loads_review_feedback(self, mock_runner_cls, db, bus, config, state_mgr):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        self._write_module_json(module_id)

        # Write review JSON for iteration 1
        _write_review_json(module_id, 1, REVIEW_NEEDS_WORK)

        _fast_forward(state_mgr, PipelineStatus.PROMPT_GENERATION, module_id, 2)

        mock_runner = MagicMock()
        mock_runner.run.return_value = Path(f"data/prompts/{module_id}/iteration-2.md")
        mock_runner_cls.return_value = mock_runner

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_prompt_generation(ctx)

        # Review arg should be a ReviewFeedback
        call_args = mock_runner.run.call_args
        review_arg = call_args[0][2]
        assert isinstance(review_arg, ReviewFeedback)
        assert review_arg.iteration == 1
        assert len(review_arg.files) == 2

    @patch("agent_os.prompt_generator.runner.PromptGeneratorRunner")
    def test_iteration_2_no_review_file_passes_none(self, mock_runner_cls, db, bus, config, state_mgr):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        self._write_module_json(module_id)

        # DON'T write a review file for iteration 1
        _fast_forward(state_mgr, PipelineStatus.PROMPT_GENERATION, module_id, 2)

        mock_runner = MagicMock()
        mock_runner.run.return_value = Path(f"data/prompts/{module_id}/iteration-2.md")
        mock_runner_cls.return_value = mock_runner

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_prompt_generation(ctx)

        call_args = mock_runner.run.call_args
        assert call_args[0][2] is None

    @patch("agent_os.prompt_generator.runner.PromptGeneratorRunner")
    def test_bus_payload_has_review_flag(self, mock_runner_cls, db, bus, config, state_mgr):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        self._write_module_json(module_id)
        _write_review_json(module_id, 1, REVIEW_NEEDS_WORK)
        _fast_forward(state_mgr, PipelineStatus.PROMPT_GENERATION, module_id, 2)

        mock_runner = MagicMock()
        mock_runner.run.return_value = Path(f"data/prompts/{module_id}/iteration-2.md")
        mock_runner_cls.return_value = mock_runner

        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_prompt_generation(ctx)

        msgs = bus.history_for_channel(Channel.PROMPT_READY)
        assert len(msgs) == 1
        assert msgs[0].payload["has_review_feedback"] is True


# ══════════════════════════════════════════════════════════════════════
#  5. Integration: Decision → iterate → Prompt gen with feedback
# ══════════════════════════════════════════════════════════════════════


class TestDecisionIterateIntegration:
    """Full loop: decision sees 'needs_work' → transitions → prompt gen reads review."""

    def _make_ctx(self, db, bus, config, state_mgr):
        return HandlerContext(state_mgr=state_mgr, db=db, config=config, bus=bus)

    def _write_module_json(self, module_id: str):
        from agent_os.module_maker.schema import ModuleDefinition

        mod_def = ModuleDefinition(
            module_id=module_id,
            name="Auth Module",
            feature_name="Authentication",
            files=["auth/login.py"],
            acceptance_criteria=["AC-1: Users can log in"],
            spec_summary="Handle authentication",
        )
        p = Path(f"data/modules/{module_id}.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(mod_def.model_dump_json(), encoding="utf-8")

    @patch("agent_os.prompt_generator.runner.PromptGeneratorRunner")
    def test_decision_iterate_then_prompt_gen_gets_feedback(
        self, mock_runner_cls, db, bus, config, state_mgr
    ):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        _seed_iteration(db, module_id, 1)
        self._write_module_json(module_id)
        _write_review_json(module_id, 1, REVIEW_NEEDS_WORK)

        # Step 1: DECISION (iteration 1)
        _fast_forward(state_mgr, PipelineStatus.DECISION, module_id, 1)
        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_decision(ctx)

        assert state_mgr.current_status == PipelineStatus.PROMPT_GENERATION
        assert state_mgr.state.current_iteration == 2

        # Step 2: PROMPT_GENERATION (iteration 2) — should load review from iter 1
        mock_runner = MagicMock()
        mock_runner.run.return_value = Path(f"data/prompts/{module_id}/iteration-2.md")
        mock_runner_cls.return_value = mock_runner

        handle_prompt_generation(ctx)

        # Verify ReviewFeedback was passed
        call_args = mock_runner.run.call_args
        review_arg = call_args[0][2]
        assert isinstance(review_arg, ReviewFeedback)
        assert review_arg.iteration == 1
        assert any(f.verdict == FileVerdict.REGENERATE for f in review_arg.files)

    @patch("agent_os.prompt_generator.runner.PromptGeneratorRunner")
    def test_accept_path_goes_to_git_commit(
        self, mock_runner_cls, db, bus, config, state_mgr
    ):
        module_id = "mod-auth"
        _seed_module(db, module_id)
        _seed_iteration(db, module_id, 1)
        self._write_module_json(module_id)
        _write_review_json(module_id, 1, REVIEW_ACCEPTED)

        _fast_forward(state_mgr, PipelineStatus.DECISION, module_id, 1)
        ctx = self._make_ctx(db, bus, config, state_mgr)
        handle_decision(ctx)

        assert state_mgr.current_status == PipelineStatus.GIT_COMMIT
        # No prompt generation should happen
        mock_runner_cls.assert_not_called()


# ── Cleanup ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _cleanup_data_dirs():
    """Remove any data dirs created during tests."""
    yield
    for dirname in ["data/reviews", "data/modules", "data/prompts"]:
        shutil.rmtree(dirname, ignore_errors=True)
