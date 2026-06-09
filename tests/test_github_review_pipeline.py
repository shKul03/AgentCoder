"""Phase 9 tests — GitHub Review pipeline integration tests.

Tests the story-queue API endpoints, OrchestratorStatusResponse GHR fields,
GitHubReviewConfig schema, and edge-case handling.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_os.api.deps import orch_holder
from agent_os.api.routes import orchestrator as orch_routes
from agent_os.config.schema import AgentOSConfig, GitHubReviewConfig
from agent_os.orchestrator.engine import Orchestrator
from agent_os.orchestrator.story_queue import StoryQueueManager, topological_sort
from agent_os.storage.database import Database
from agent_os.storage.models import StoryStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _build_no_deps(mgr: StoryQueueManager, stories: list[dict]) -> list:
    async def _fake(strs, **kwargs):
        return [{**s, "depends_on": [], "dependency_reason": ""} for s in strs]

    with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fake):
        return _run(mgr.build_queue(stories))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_config(tmp_path: Path) -> AgentOSConfig:
    return AgentOSConfig(
        storage={"db_path": str(tmp_path / "test.db")},
        secrets={"openai_api_key": "sk-test", "github_token": "ghp_test"},
        github={"owner": "test-owner", "repo": "test-repo"},
        pipeline_mode="github_review",
    )


@pytest.fixture()
def tmp_standard_config(tmp_path: Path) -> AgentOSConfig:
    return AgentOSConfig(
        storage={"db_path": str(tmp_path / "std.db")},
        secrets={"openai_api_key": "sk-test"},
    )


@pytest.fixture()
def api_client(tmp_config: AgentOSConfig):
    """Build a minimal FastAPI TestClient with the orchestrator router."""
    orch_holder.shutdown()
    orch_holder.init(tmp_config)

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    app = FastAPI(lifespan=noop_lifespan)
    app.include_router(orch_routes.router)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    orch_holder.shutdown()


@pytest.fixture()
def standard_api_client(tmp_standard_config: AgentOSConfig):
    """TestClient in standard (non-GHR) mode."""
    orch_holder.shutdown()
    orch_holder.init(tmp_standard_config)

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    app = FastAPI(lifespan=noop_lifespan)
    app.include_router(orch_routes.router)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    orch_holder.shutdown()


# ---------------------------------------------------------------------------
# 1. GitHubReviewConfig schema
# ---------------------------------------------------------------------------


class TestGitHubReviewConfig:
    def test_defaults(self):
        cfg = GitHubReviewConfig()
        assert cfg.source_repo_url == ""
        assert cfg.requirements_path == ""
        assert cfg.fork_repo_name == ""
        assert cfg.branch_name == "agent-os-fixes"

    def test_custom_values(self):
        cfg = GitHubReviewConfig(
            source_repo_url="https://github.com/owner/repo",
            requirements_path="data/requirements.yaml",
            fork_repo_name="repo-fork",
            branch_name="story-",
        )
        assert cfg.source_repo_url == "https://github.com/owner/repo"
        assert cfg.branch_name == "story-"

    def test_config_pipeline_mode_default(self):
        cfg = AgentOSConfig()
        assert cfg.pipeline_mode == "standard"

    def test_config_pipeline_mode_github_review(self):
        cfg = AgentOSConfig(pipeline_mode="github_review")
        assert cfg.pipeline_mode == "github_review"


# ---------------------------------------------------------------------------
# 2. GET /api/orchestrator/status — new GHR fields
# ---------------------------------------------------------------------------


class TestStatusEndpointGHRFields:
    def test_status_includes_mode_field(self, api_client):
        resp = api_client.get("/api/orchestrator/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert data["mode"] == "github_review"

    def test_status_includes_story_fields(self, api_client):
        resp = api_client.get("/api/orchestrator/status")
        data = resp.json()
        assert "current_story_id" in data
        assert "stories_completed" in data
        assert "stories_total" in data
        assert data["current_story_id"] is None
        assert data["stories_completed"] == 0
        assert data["stories_total"] == 0

    def test_status_mode_standard_when_not_ghr(self, standard_api_client):
        resp = standard_api_client.get("/api/orchestrator/status")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "standard"


# ---------------------------------------------------------------------------
# 3. GET /api/orchestrator/story-queue
# ---------------------------------------------------------------------------


class TestGetStoryQueueEndpoint:
    def test_empty_queue_returns_structure(self, api_client):
        resp = api_client.get("/api/orchestrator/story-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert "stories" in data
        assert data["stories"] == []
        assert data["mode"] == "github_review"

    def test_queue_with_stories(self, api_client, tmp_config):
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        _build_no_deps(mgr, [
            {"story_id": "S1", "title": "Auth"},
            {"story_id": "S2", "title": "Dashboard"},
        ])
        db.close()

        resp = api_client.get("/api/orchestrator/story-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["stories"]) == 2
        assert data["stories"][0]["story_id"] == "S1"
        assert data["stories"][1]["story_id"] == "S2"

    def test_queue_response_has_stories_counts(self, api_client):
        resp = api_client.get("/api/orchestrator/story-queue")
        data = resp.json()
        assert "stories_completed" in data
        assert "stories_total" in data
        assert "current_story_id" in data


# ---------------------------------------------------------------------------
# 4. GET /api/orchestrator/story-queue/{story_id}
# ---------------------------------------------------------------------------


class TestGetStoryQueueItemEndpoint:
    def _seed_queue(self, tmp_config, stories):
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        _build_no_deps(mgr, stories)
        db.close()

    def test_returns_story_detail(self, api_client, tmp_config):
        self._seed_queue(tmp_config, [
            {
                "story_id": "STORY-1",
                "title": "User Login",
                "description": "As a user I want to log in",
                "acceptance_criteria": ["Login form exists", "JWT issued on success"],
            }
        ])
        resp = api_client.get("/api/orchestrator/story-queue/STORY-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["story_id"] == "STORY-1"
        assert data["title"] == "User Login"
        assert data["acceptance_criteria"] == ["Login form exists", "JWT issued on success"]
        assert data["status"] == "queued"

    def test_returns_404_for_unknown_story(self, api_client):
        resp = api_client.get("/api/orchestrator/story-queue/NONEXISTENT")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_branch_and_pr_fields_returned(self, api_client, tmp_config):
        self._seed_queue(tmp_config, [{"story_id": "BR-1", "title": "Branch test"}])

        # Manually set branch + pr info
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        mgr.update_branch("BR-1", "story-br1-feature")
        mgr.dequeue()
        mgr.mark_complete("BR-1", pr_number=7, pr_url="https://github.com/pr/7")
        db.close()

        resp = api_client.get("/api/orchestrator/story-queue/BR-1")
        data = resp.json()
        assert data["branch_name"] == "story-br1-feature"
        assert data["pr_number"] == 7
        assert data["pr_url"] == "https://github.com/pr/7"
        assert data["status"] == "completed"

    def test_depends_on_returned(self, api_client, tmp_config):
        stories = [
            {"story_id": "DEP-A", "title": "Auth"},
            {"story_id": "DEP-B", "title": "Dashboard"},
        ]

        async def _fake_with_dep(strs, **kwargs):
            return [
                {**strs[0], "depends_on": [], "dependency_reason": ""},
                {**strs[1], "depends_on": ["DEP-A"], "dependency_reason": "Needs auth"},
            ]

        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fake_with_dep):
            _run(mgr.build_queue(stories))
        db.close()

        resp = api_client.get("/api/orchestrator/story-queue/DEP-B")
        data = resp.json()
        assert "DEP-A" in data["depends_on"]
        assert "Needs auth" in data["dependency_reason"]


# ---------------------------------------------------------------------------
# 5. POST /api/orchestrator/story-queue/reorder
# ---------------------------------------------------------------------------


class TestReorderStoryQueueEndpoint:
    def _seed(self, tmp_config, story_ids):
        stories = [{"story_id": sid, "title": sid} for sid in story_ids]
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        _build_no_deps(mgr, stories)
        db.close()

    def test_reorder_changes_positions(self, api_client, tmp_config):
        self._seed(tmp_config, ["A", "B", "C"])

        resp = api_client.post(
            "/api/orchestrator/story-queue/reorder",
            json={"story_ids": ["C", "A", "B"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is True

        # Verify positions changed in DB
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        queue = mgr.get_queue_state()
        db.close()

        pos = {row["story_id"]: row["position"] for row in queue}
        assert pos["C"] == 0
        assert pos["A"] == 1
        assert pos["B"] == 2

    def test_reorder_skips_non_queued_stories(self, api_client, tmp_config):
        self._seed(tmp_config, ["X", "Y"])

        # Mark X as in_progress
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        mgr.dequeue()  # X → in_progress
        db.close()

        # Attempt reorder: X is in_progress, should not be repositioned
        resp = api_client.post(
            "/api/orchestrator/story-queue/reorder",
            json={"story_ids": ["Y", "X"]},
        )
        assert resp.status_code == 200
        # Only Y is queued → 1 updated
        assert "1" in resp.json()["message"]

    def test_reorder_empty_list_is_no_op(self, api_client, tmp_config):
        self._seed(tmp_config, ["P", "Q"])
        resp = api_client.post(
            "/api/orchestrator/story-queue/reorder",
            json={"story_ids": []},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. Story queue building — queue ordering scenarios
# ---------------------------------------------------------------------------


class TestQueueBuildingScenarios:
    def test_single_story_queue(self, tmp_path):
        db = Database(str(tmp_path / "db.sqlite"))
        db.connect()
        mgr = StoryQueueManager(db)
        items = _build_no_deps(mgr, [{"story_id": "ONLY", "title": "Solo"}])
        db.close()
        assert len(items) == 1
        assert items[0].story_id == "ONLY"
        assert items[0].position == 0

    def test_no_dependencies_alphabetical_stable(self, tmp_path):
        db = Database(str(tmp_path / "db.sqlite"))
        db.connect()
        mgr = StoryQueueManager(db)
        items = _build_no_deps(mgr, [
            {"story_id": "Z", "title": "Z story"},
            {"story_id": "A", "title": "A story"},
            {"story_id": "M", "title": "M story"},
        ])
        db.close()
        # Original order is preserved when no deps
        assert [i.story_id for i in items] == ["Z", "A", "M"]

    def test_linear_dependency_chain(self, tmp_path):
        db = Database(str(tmp_path / "db.sqlite"))
        db.connect()
        mgr = StoryQueueManager(db)
        stories = [
            {"story_id": "C", "title": "C"},
            {"story_id": "A", "title": "A"},
            {"story_id": "B", "title": "B"},
        ]

        async def _fake(strs, **kwargs):
            id_map = {s["story_id"]: s for s in strs}
            return [
                {**id_map["A"], "depends_on": [], "dependency_reason": ""},
                {**id_map["B"], "depends_on": ["A"], "dependency_reason": ""},
                {**id_map["C"], "depends_on": ["B"], "dependency_reason": ""},
            ]

        with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fake):
            items = _run(mgr.build_queue(stories))
        db.close()

        ids = [i.story_id for i in items]
        assert ids.index("A") < ids.index("B") < ids.index("C")

    def test_diamond_dependency(self, tmp_path):
        db = Database(str(tmp_path / "db.sqlite"))
        db.connect()
        mgr = StoryQueueManager(db)
        stories = [
            {"story_id": "ROOT", "title": "Root"},
            {"story_id": "LEFT", "title": "Left"},
            {"story_id": "RIGHT", "title": "Right"},
            {"story_id": "TIP", "title": "Tip"},
        ]

        async def _fake(strs, **kwargs):
            m = {s["story_id"]: s for s in strs}
            return [
                {**m["ROOT"], "depends_on": [], "dependency_reason": ""},
                {**m["LEFT"], "depends_on": ["ROOT"], "dependency_reason": ""},
                {**m["RIGHT"], "depends_on": ["ROOT"], "dependency_reason": ""},
                {**m["TIP"], "depends_on": ["LEFT", "RIGHT"], "dependency_reason": ""},
            ]

        with patch("agent_os.orchestrator.story_queue.analyse_dependencies", side_effect=_fake):
            items = _run(mgr.build_queue(stories))
        db.close()

        ids = [i.story_id for i in items]
        assert ids.index("ROOT") < ids.index("LEFT")
        assert ids.index("ROOT") < ids.index("RIGHT")
        assert ids.index("LEFT") < ids.index("TIP")
        assert ids.index("RIGHT") < ids.index("TIP")


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_status_endpoint_stories_total_reflects_queue(self, api_client, tmp_config):
        """stories_total in /status should reflect real DB state via state_mgr."""
        # The state manager persists stories_total when the engine runs;
        # here we verify it starts at 0 without any pipeline run.
        resp = api_client.get("/api/orchestrator/status")
        data = resp.json()
        assert data["stories_total"] == 0

    def test_story_queue_detail_acceptance_criteria_list(self, api_client, tmp_config):
        """Ensure acceptance_criteria is always returned as a list, never null."""
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        _build_no_deps(mgr, [{"story_id": "NO-AC", "title": "No AC story"}])
        db.close()

        resp = api_client.get("/api/orchestrator/story-queue/NO-AC")
        data = resp.json()
        assert isinstance(data["acceptance_criteria"], list)

    def test_story_queue_item_iteration_counter(self, api_client, tmp_config):
        """story_iteration starts at 0 and increments correctly."""
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        _build_no_deps(mgr, [{"story_id": "ITER", "title": "Iter test"}])
        mgr.increment_iteration("ITER")
        mgr.increment_iteration("ITER")
        db.close()

        resp = api_client.get("/api/orchestrator/story-queue/ITER")
        assert resp.json()["story_iteration"] == 2

    def test_reorder_with_unknown_story_ids_is_safe(self, api_client, tmp_config):
        """Reorder with IDs not in the DB should not crash (graceful no-op)."""
        resp = api_client.post(
            "/api/orchestrator/story-queue/reorder",
            json={"story_ids": ["GHOST-1", "GHOST-2"]},
        )
        assert resp.status_code == 200
        assert resp.json()["approved"] is True

    def test_story_queue_returns_complete_story_fields(self, api_client, tmp_config):
        """Verify the response model includes all expected fields."""
        db = Database(tmp_config.storage.db_path)
        db.connect()
        mgr = StoryQueueManager(db)
        _build_no_deps(mgr, [{"story_id": "FULL", "title": "Full fields"}])
        db.close()

        resp = api_client.get("/api/orchestrator/story-queue/FULL")
        data = resp.json()
        expected_fields = {
            "story_id", "title", "description", "acceptance_criteria",
            "position", "status", "branch_name", "pr_number", "pr_url",
            "story_iteration", "depends_on", "dependency_reason",
            "created_at", "completed_at",
        }
        assert expected_fields.issubset(set(data.keys()))
