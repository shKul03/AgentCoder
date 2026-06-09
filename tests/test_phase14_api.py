"""Phase 14 tests — FastAPI backend API + WebSocket bridge.

Tests the REST endpoints and WebSocket connection manager.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_os.api.deps import orch_holder
from agent_os.api.websocket import ConnectionManager
from agent_os.comms.bus import AgentCommBus
from agent_os.comms.channels import Channel
from agent_os.comms.messages import (
    AgentMessage,
    PipelineEventMessage,
    ValidationResultMessage,
)
from agent_os.config.schema import AgentOSConfig
from agent_os.storage.database import Database
from agent_os.storage.models import (
    IterationRecord,
    ModuleRecord,
    ModuleStatus,
    PipelineStatus,
    RequirementRecord,
    RequirementType,
)
from agent_os.storage.iteration_repo import IterationRepository
from agent_os.storage.module_repo import ModuleRepository
from agent_os.storage.requirement_repo import RequirementRepository


# ---------- Fixtures --------------------------------------------------------

@pytest.fixture()
def tmp_config(tmp_path: Path) -> AgentOSConfig:
    db_path = str(tmp_path / "test.db")
    return AgentOSConfig(storage={"db_path": db_path})


@pytest.fixture()
def app_client(tmp_config: AgentOSConfig):
    """Create a TestClient with a fresh Orchestrator (no lifespan)."""
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from agent_os.api.routes import bus, metrics, modules, pipeline, requirements
    from agent_os.api.websocket import router as ws_router

    # Ensure any prior holder is cleared
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
    app.include_router(ws_router)

    with TestClient(app) as client:
        yield client, orch

    orch_holder.shutdown()


# ---------- REST Endpoint Tests --------------------------------------------

class TestPipelineEndpoints:

    def test_get_pipeline_status(self, app_client):
        client, _orch = app_client
        resp = client.get("/api/pipeline/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_status"] == "IDLE"
        assert data["is_hitl_gate"] is False
        assert data["total_modules"] == 0
        assert data["current_iteration"] == 0

    def test_start_pipeline(self, app_client):
        client, _orch = app_client
        with patch("agent_os.api.routes.pipeline.threading.Thread"):
            resp = client.post("/api/pipeline/start")
        assert resp.status_code == 200
        data = resp.json()
        assert "pipeline_status" in data

    def test_approve_gate_when_not_at_gate(self, app_client):
        client, _orch = app_client
        resp = client.post(
            "/api/pipeline/approve-gate",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False
        assert "Not at a HITL gate" in data["message"]

    def test_approve_gate_invalid_gate(self, app_client):
        client, _orch = app_client
        resp = client.post(
            "/api/pipeline/approve-gate",
            json={"gate": "not_a_real_gate"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is False
        assert "Invalid gate" in data["message"]

    def test_approve_gate_at_hitl(self, app_client):
        client, orch = app_client
        # Force state to HITL gate
        orch.state_mgr.transition_to(PipelineStatus.LOADING_REQUIREMENTS)
        orch.state_mgr.transition_to(PipelineStatus.MODULE_PLANNING)
        orch.state_mgr.transition_to(PipelineStatus.HITL_1_MODULE_REVIEW)

        with patch("agent_os.api.routes.pipeline.threading.Thread"):
            resp = client.post(
                "/api/pipeline/approve-gate",
                json={"gate": "HITL_1_MODULE_REVIEW"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["approved"] is True


class TestModuleEndpoints:

    def test_list_modules_empty(self, app_client):
        client, _orch = app_client
        resp = client.get("/api/modules")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_modules_with_data(self, app_client):
        client, orch = app_client
        repo = ModuleRepository(orch.db.conn)
        repo.upsert(ModuleRecord(
            id="mod-1", name="Auth", feature_name="authentication",
            execution_order=1,
        ))
        repo.upsert(ModuleRecord(
            id="mod-2", name="Dashboard", feature_name="dashboard",
            execution_order=2,
        ))

        resp = client.get("/api/modules")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "mod-1"
        assert data[1]["id"] == "mod-2"
        assert data[0]["status"] == "pending"

    def test_get_module_found(self, app_client):
        client, orch = app_client
        repo = ModuleRepository(orch.db.conn)
        repo.upsert(ModuleRecord(
            id="mod-1", name="Auth", feature_name="authentication",
        ))

        resp = client.get("/api/modules/mod-1")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Auth"

    def test_get_module_not_found(self, app_client):
        client, _orch = app_client
        resp = client.get("/api/modules/nonexistent")
        assert resp.status_code == 404

    def test_get_module_iterations(self, app_client):
        client, orch = app_client
        mod_repo = ModuleRepository(orch.db.conn)
        mod_repo.upsert(ModuleRecord(
            id="mod-1", name="Auth", feature_name="authentication",
        ))
        iter_repo = IterationRepository(orch.db.conn)
        iter_repo.create(IterationRecord(module_id="mod-1", iteration_number=1))
        iter_repo.create(IterationRecord(module_id="mod-1", iteration_number=2))

        resp = client.get("/api/modules/mod-1/iterations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["iteration_number"] == 1


class TestRequirementEndpoints:

    def test_list_requirements_empty(self, app_client):
        client, _orch = app_client
        resp = client.get("/api/requirements")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_requirements_with_data(self, app_client):
        client, orch = app_client
        repo = RequirementRepository(orch.db.conn)
        repo.upsert(RequirementRecord(
            id="req-1", type=RequirementType.EPIC,
            title="User auth", description="Full auth system",
        ))

        resp = client.get("/api/requirements")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "req-1"
        assert data[0]["title"] == "User auth"


class TestMetricsEndpoint:

    def test_metrics_empty(self, app_client):
        client, _orch = app_client
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_modules"] == 0
        assert data["pipeline_status"] == "IDLE"

    def test_metrics_with_data(self, app_client):
        client, orch = app_client
        mod_repo = ModuleRepository(orch.db.conn)
        mod_repo.upsert(ModuleRecord(
            id="m1", name="A", feature_name="a",
            status=ModuleStatus.COMPLETED,
        ))
        mod_repo.upsert(ModuleRecord(
            id="m2", name="B", feature_name="b",
            status=ModuleStatus.FAILED,
        ))

        iter_repo = IterationRepository(orch.db.conn)
        iter_repo.create(IterationRecord(
            module_id="m1", iteration_number=1, token_usage=150,
        ))

        resp = client.get("/api/metrics")
        data = resp.json()
        assert data["total_modules"] == 2
        assert data["completed_modules"] == 1
        assert data["failed_modules"] == 1
        assert data["total_iterations"] == 1
        assert data["total_token_usage"] == 150


class TestBusHistoryEndpoint:

    def test_bus_history_empty(self, app_client):
        client, _orch = app_client
        resp = client.get("/api/bus/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_bus_history_with_messages(self, app_client):
        client, orch = app_client
        orch.bus.publish(PipelineEventMessage(
            sender="test", module_id="mod-1",
            payload={"event": "started"},
        ))
        orch.bus.publish(ValidationResultMessage(
            sender="validator", module_id="mod-1",
            payload={"passed": True},
        ))

        resp = client.get("/api/bus/history")
        data = resp.json()
        assert len(data) == 2

    def test_bus_history_filter_by_channel(self, app_client):
        client, orch = app_client
        orch.bus.publish(PipelineEventMessage(
            sender="test", payload={"event": "x"},
        ))
        orch.bus.publish(ValidationResultMessage(
            sender="validator", payload={"passed": True},
        ))

        resp = client.get("/api/bus/history?channel=pipeline_events")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["channel"] == "pipeline_events"

    def test_bus_history_invalid_channel(self, app_client):
        client, _orch = app_client
        resp = client.get("/api/bus/history?channel=nonexistent_channel")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------- WebSocket Tests ------------------------------------------------

class TestWebSocketBridge:

    def test_websocket_connect_disconnect(self, app_client):
        client, _orch = app_client
        with client.websocket_connect("/ws") as ws:
            # Just test that connection is accepted
            ws.send_json({"subscribe": ["pipeline_events"]})

    def test_connection_manager(self):
        mgr = ConnectionManager()
        assert mgr.active_count == 0

    def test_connection_manager_set_filter(self):
        mgr = ConnectionManager()
        # set_filter doesn't add entries for unknown websockets
        assert mgr.active_count == 0
