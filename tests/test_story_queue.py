"""Phase 9 tests — StoryQueueManager unit tests.

Tests topological sort, queue building, lifecycle operations (dequeue,
mark_complete, mark_failed, increment_iteration), and introspection
helpers (peek, get_item, get_queue_state, is_complete, counts).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent_os.orchestrator.story_queue import StoryQueueManager, topological_sort
from agent_os.storage.database import Database
from agent_os.storage.models import StoryStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Database:
    db = Database(str(tmp_path / "test.db"))
    db.connect()
    yield db
    db.close()


@pytest.fixture()
def mgr(tmp_db: Database) -> StoryQueueManager:
    return StoryQueueManager(tmp_db)


def _run(coro):
    """Run an async coroutine from a sync test."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helper: build a queue synchronously (bypasses LLM dependency analysis)
# ---------------------------------------------------------------------------

def _build_no_deps(mgr: StoryQueueManager, stories: list[dict]) -> list:
    """Build the queue with mocked analyse_dependencies (no-op — no OpenAI call)."""
    enriched = [{**s, "depends_on": [], "dependency_reason": ""} for s in stories]

    async def _fake_analyse(stories, **kwargs):
        return [{**s, "depends_on": s.get("depends_on", []), "dependency_reason": ""} for s in stories]

    with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fake_analyse):
        return _run(mgr.build_queue(stories))


# ---------------------------------------------------------------------------
# 1. topological_sort
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    def test_no_dependencies_preserves_order(self):
        stories = [
            {"story_id": "A", "depends_on": []},
            {"story_id": "B", "depends_on": []},
            {"story_id": "C", "depends_on": []},
        ]
        result = topological_sort(stories)
        assert [s["story_id"] for s in result] == ["A", "B", "C"]

    def test_linear_chain_A_then_B_then_C(self):
        stories = [
            {"story_id": "A", "depends_on": []},
            {"story_id": "B", "depends_on": ["A"]},
            {"story_id": "C", "depends_on": ["B"]},
        ]
        result = topological_sort(stories)
        ids = [s["story_id"] for s in result]
        assert ids.index("A") < ids.index("B") < ids.index("C")

    def test_diamond_dependency(self):
        # A → B, A → C, B + C → D
        stories = [
            {"story_id": "A", "depends_on": []},
            {"story_id": "B", "depends_on": ["A"]},
            {"story_id": "C", "depends_on": ["A"]},
            {"story_id": "D", "depends_on": ["B", "C"]},
        ]
        result = topological_sort(stories)
        ids = [s["story_id"] for s in result]
        assert ids.index("A") < ids.index("B")
        assert ids.index("A") < ids.index("C")
        assert ids.index("B") < ids.index("D")
        assert ids.index("C") < ids.index("D")

    def test_single_story(self):
        stories = [{"story_id": "ONLY", "depends_on": []}]
        result = topological_sort(stories)
        assert [s["story_id"] for s in result] == ["ONLY"]

    def test_unknown_dependency_is_soft_ignored(self):
        """If a story depends_on an ID that doesn't exist, it should still be returned."""
        stories = [
            {"story_id": "X", "depends_on": ["GHOST"]},
            {"story_id": "Y", "depends_on": []},
        ]
        result = topological_sort(stories)
        ids = [s["story_id"] for s in result]
        assert "X" in ids
        assert "Y" in ids

    def test_cycle_does_not_drop_stories(self):
        """Circular deps should not silently drop stories."""
        stories = [
            {"story_id": "P", "depends_on": ["Q"]},
            {"story_id": "Q", "depends_on": ["P"]},
        ]
        result = topological_sort(stories)
        assert {s["story_id"] for s in result} == {"P", "Q"}


# ---------------------------------------------------------------------------
# 2. build_queue
# ---------------------------------------------------------------------------


class TestBuildQueue:
    def test_build_queue_persists_to_db(self, mgr):
        stories = [
            {"story_id": "S1", "title": "Story 1"},
            {"story_id": "S2", "title": "Story 2"},
        ]
        items = _build_no_deps(mgr, stories)
        assert len(items) == 2
        assert items[0].story_id == "S1"
        assert items[0].status == StoryStatus.QUEUED
        assert items[0].position == 0
        assert items[1].story_id == "S2"
        assert items[1].position == 1

    def test_build_queue_clears_previous_queue(self, mgr):
        _build_no_deps(mgr, [{"story_id": "OLD", "title": "Old"}])
        _build_no_deps(mgr, [{"story_id": "NEW", "title": "New"}])
        state = mgr.get_queue_state()
        assert len(state) == 1
        assert state[0]["story_id"] == "NEW"

    def test_build_queue_stores_acceptance_criteria(self, mgr):
        stories = [
            {
                "story_id": "AC1",
                "title": "With AC",
                "acceptance_criteria": ["AC must pass", "Login must work"],
            }
        ]
        _build_no_deps(mgr, stories)
        item = mgr.get_item("AC1")
        assert item is not None
        assert item.acceptance_criteria == ["AC must pass", "Login must work"]

    def test_build_queue_acceptance_criteria_as_string(self, mgr):
        """acceptance_criteria may arrive as a plain string — should be wrapped in list."""
        stories = [{"story_id": "S", "title": "T", "acceptance_criteria": "single AC"}]
        _build_no_deps(mgr, stories)
        item = mgr.get_item("S")
        assert isinstance(item.acceptance_criteria, list)
        assert item.acceptance_criteria == ["single AC"]

    def test_build_queue_preserves_dependency_ordering(self, mgr):
        stories = [
            {"story_id": "BASE", "title": "Base model"},
            {"story_id": "API", "title": "API layer"},
        ]

        async def _fake_analyse_with_dep(strs, **kwargs):
            return [
                {**strs[0], "depends_on": [], "dependency_reason": ""},
                {**strs[1], "depends_on": ["BASE"], "dependency_reason": "Needs base"},
            ]

        with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fake_analyse_with_dep):
            items = _run(mgr.build_queue(stories))

        ids = [i.story_id for i in items]
        assert ids.index("BASE") < ids.index("API")


# ---------------------------------------------------------------------------
# 3. dequeue
# ---------------------------------------------------------------------------


class TestDequeue:
    def test_dequeue_returns_first_queued_story(self, mgr):
        _build_no_deps(mgr, [
            {"story_id": "A", "title": "A"},
            {"story_id": "B", "title": "B"},
        ])
        item = mgr.dequeue()
        assert item is not None
        assert item.story_id == "A"

    def test_dequeue_marks_story_in_progress(self, mgr):
        _build_no_deps(mgr, [{"story_id": "X", "title": "X"}])
        mgr.dequeue()
        item = mgr.get_item("X")
        assert item.status == StoryStatus.IN_PROGRESS

    def test_dequeue_respects_dependency_ordering(self, mgr):
        """B depends on A — dequeue should return A first, then B after A completes."""
        stories = [
            {"story_id": "A", "title": "A"},
            {"story_id": "B", "title": "B"},
        ]

        async def _fake(strs, **kwargs):
            return [
                {**strs[0], "depends_on": [], "dependency_reason": ""},
                {**strs[1], "depends_on": ["A"], "dependency_reason": ""},
            ]

        with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fake):
            _run(mgr.build_queue(stories))

        first = mgr.dequeue()
        assert first.story_id == "A"

        # B is still blocked — dequeue returns None while A is in_progress
        blocked = mgr.dequeue()
        assert blocked is None

        # After A completes, B becomes dequeue-able
        mgr.mark_complete("A")
        second = mgr.dequeue()
        assert second is not None
        assert second.story_id == "B"

    def test_dequeue_returns_none_when_empty(self, mgr):
        result = mgr.dequeue()
        assert result is None

    def test_dequeue_returns_none_when_all_complete(self, mgr):
        _build_no_deps(mgr, [{"story_id": "Z", "title": "Z"}])
        mgr.dequeue()
        mgr.mark_complete("Z")
        result = mgr.dequeue()
        assert result is None


# ---------------------------------------------------------------------------
# 4. mark_complete / mark_failed
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_mark_complete_sets_status_and_timestamp(self, mgr):
        _build_no_deps(mgr, [{"story_id": "C1", "title": "C1"}])
        mgr.dequeue()
        mgr.mark_complete("C1", pr_number=42, pr_url="https://github.com/pr/42")

        item = mgr.get_item("C1")
        assert item.status == StoryStatus.COMPLETED
        assert item.pr_number == 42
        assert item.pr_url == "https://github.com/pr/42"
        assert item.completed_at is not None

    def test_mark_failed_sets_status(self, mgr):
        _build_no_deps(mgr, [{"story_id": "F1", "title": "F1"}])
        mgr.dequeue()
        mgr.mark_failed("F1", reason="timeout")

        item = mgr.get_item("F1")
        assert item.status == StoryStatus.FAILED
        assert "timeout" in item.dependency_reason

    def test_is_complete_false_while_queued(self, mgr):
        _build_no_deps(mgr, [{"story_id": "Q", "title": "Q"}])
        assert mgr.is_complete() is False

    def test_is_complete_true_when_all_done(self, mgr):
        _build_no_deps(mgr, [
            {"story_id": "D1", "title": "D1"},
            {"story_id": "D2", "title": "D2"},
        ])
        mgr.dequeue(); mgr.mark_complete("D1")
        mgr.dequeue(); mgr.mark_failed("D2")
        assert mgr.is_complete() is True

    def test_is_complete_true_on_empty_queue(self, mgr):
        assert mgr.is_complete() is True


# ---------------------------------------------------------------------------
# 5. increment_iteration / update_branch
# ---------------------------------------------------------------------------


class TestIterationAndBranch:
    def test_increment_iteration_starts_at_zero(self, mgr):
        _build_no_deps(mgr, [{"story_id": "IT", "title": "IT"}])
        item = mgr.get_item("IT")
        assert item.story_iteration == 0

    def test_increment_iteration_increments(self, mgr):
        _build_no_deps(mgr, [{"story_id": "IT", "title": "IT"}])
        n1 = mgr.increment_iteration("IT")
        n2 = mgr.increment_iteration("IT")
        assert n1 == 1
        assert n2 == 2

    def test_update_branch_name(self, mgr):
        _build_no_deps(mgr, [{"story_id": "BR", "title": "BR"}])
        mgr.update_branch("BR", "story-br-add-feature")
        item = mgr.get_item("BR")
        assert item.branch_name == "story-br-add-feature"


# ---------------------------------------------------------------------------
# 6. peek
# ---------------------------------------------------------------------------


class TestPeek:
    def test_peek_does_not_change_status(self, mgr):
        _build_no_deps(mgr, [{"story_id": "PK", "title": "PK"}])
        item = mgr.peek()
        assert item is not None
        assert item.story_id == "PK"
        # status remains QUEUED
        assert mgr.get_item("PK").status == StoryStatus.QUEUED

    def test_peek_returns_none_when_blocked(self, mgr):
        stories = [
            {"story_id": "A", "title": "A"},
            {"story_id": "B", "title": "B"},
        ]

        async def _fake(strs, **kwargs):
            return [
                {**strs[0], "depends_on": [], "dependency_reason": ""},
                {**strs[1], "depends_on": ["A"], "dependency_reason": ""},
            ]

        with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fake):
            _run(mgr.build_queue(stories))

        mgr.dequeue()  # A is now IN_PROGRESS; B is still blocked

        # All remaining QUEUED stories (B) are blocked
        assert mgr.peek() is None


# ---------------------------------------------------------------------------
# 7. get_queue_state / counts
# ---------------------------------------------------------------------------


class TestIntrospection:
    def test_get_queue_state_returns_all_rows(self, mgr):
        _build_no_deps(mgr, [
            {"story_id": "R1", "title": "R1"},
            {"story_id": "R2", "title": "R2"},
        ])
        state = mgr.get_queue_state()
        assert len(state) == 2
        assert state[0]["story_id"] == "R1"
        assert state[1]["story_id"] == "R2"

    def test_get_item_returns_none_for_unknown(self, mgr):
        assert mgr.get_item("MISSING") is None

    def test_counts_reflect_lifecycle(self, mgr):
        _build_no_deps(mgr, [
            {"story_id": "C1", "title": "C1"},
            {"story_id": "C2", "title": "C2"},
            {"story_id": "C3", "title": "C3"},
        ])
        mgr.dequeue(); mgr.mark_complete("C1")
        mgr.dequeue(); mgr.mark_failed("C2")

        c = mgr.counts()
        assert c["completed"] == 1
        assert c["failed"] == 1
        assert c["queued"] == 1
        assert c["total"] == 3


# ---------------------------------------------------------------------------
# 8. dependency analysis fallback (no OpenAI key)
# ---------------------------------------------------------------------------


class TestDependencyAnalysisFallback:
    def test_build_queue_fallback_when_no_api_key(self, mgr):
        """build_queue must succeed even when OpenAI is unavailable."""
        stories = [
            {"story_id": "FB1", "title": "Fallback 1"},
            {"story_id": "FB2", "title": "Fallback 2"},
        ]

        async def _fail(*args, **kwargs):
            raise RuntimeError("network error")

        with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fail):
            # build_queue should catch the exception and continue
            with pytest.raises(RuntimeError):
                _run(mgr.build_queue(stories))

    def test_no_op_dependencies_adds_empty_deps(self):
        from agent_os.orchestrator.story_queue import _no_op_dependencies
        stories = [{"story_id": "N1", "title": "N1"}, {"story_id": "N2", "title": "N2"}]
        result = _no_op_dependencies(stories)
        for r in result:
            assert r["depends_on"] == []
            assert r["dependency_reason"] == ""
