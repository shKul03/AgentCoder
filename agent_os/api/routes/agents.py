"""Agents routes — CRUD for agent identity files and registry mapping."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...agents.store import (
    AGENT_FILES,
    AgentIdentityStore,
    AgentNotFoundError,
    AgentRegistry,
)
from ...storage.agent_config_repo import AgentConfigRepo
from ..deps import get_orchestrator
from ..schemas import (
    AgentDetailResponse,
    AgentFileResponse,
    AgentListResponse,
    AgentRegistryResponse,
    CreateAgentRequest,
    UpdateAgentFileRequest,
    UpdateRegistryRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["agents"])

_AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


def _store() -> AgentIdentityStore:
    return AgentIdentityStore(_AGENTS_DIR)


def _registry() -> AgentRegistry:
    return AgentRegistry(_AGENTS_DIR / "registry.json")


def _cfg_repo(orch=None) -> AgentConfigRepo:
    """Return an AgentConfigRepo using the shared DB connection."""
    if orch is None:
        from ..deps import orch_holder
        orch = orch_holder.orchestrator
    return AgentConfigRepo(orch.db.conn)


# ---------------------------------------------------------------------------
# List all agents
# ---------------------------------------------------------------------------


@router.get("", response_model=AgentListResponse)
def list_agents():
    """Return metadata for every agent (built-in and custom)."""
    agents = _store().list_agents()
    return AgentListResponse(agents=agents)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@router.get("/registry", response_model=AgentRegistryResponse)
def get_registry():
    """Return the current pipeline-post → agent mapping."""
    return AgentRegistryResponse(mapping=_registry().get_registry())


@router.put("/registry", response_model=AgentRegistryResponse)
def update_registry(body: UpdateRegistryRequest, orch=Depends(get_orchestrator)):
    """Update one or more pipeline-post → agent mappings and persist to DB."""
    reg = _registry()
    reg.update_registry(body.mapping)
    full_mapping = reg.get_registry()
    # Mirror to DB
    _cfg_repo(orch).set_registry(full_mapping)
    return AgentRegistryResponse(mapping=full_mapping)


# ---------------------------------------------------------------------------
# Model routing — dedicated endpoint (also mirrors to DB)
# ---------------------------------------------------------------------------


class ModelRoutingBody(BaseModel):
    model_routing: dict[str, str]


class ModelRoutingResponse(BaseModel):
    model_routing: dict[str, str]


@router.get("/model-routing", response_model=ModelRoutingResponse)
def get_model_routing(orch=Depends(get_orchestrator)):
    """Return current model routing (DB first, then config fallback)."""
    repo = _cfg_repo(orch)
    routing = repo.get_model_routing()
    if routing is None:
        # Fall back to live config values and seed the DB
        routing = dict(orch.config.codex.model_routing)
        repo.set_model_routing(routing)
    return ModelRoutingResponse(model_routing=routing)


@router.put("/model-routing", response_model=ModelRoutingResponse)
def update_model_routing(body: ModelRoutingBody, orch=Depends(get_orchestrator)):
    """Save model routing to config.yaml AND the database."""
    routing = body.model_routing
    # Apply to live config
    orch.config.codex.model_routing.update(routing)
    # Write config.yaml
    try:
        from ..deps import orch_holder
        from .settings import _write_config_yaml
        _write_config_yaml(orch.config, orch_holder.config_path)
    except Exception as exc:
        logger.warning("Could not persist model routing to config.yaml: %s", exc)
    # Mirror to DB (also seeds defaults on first call)
    _cfg_repo(orch).set_model_routing(dict(orch.config.codex.model_routing))
    return ModelRoutingResponse(model_routing=dict(orch.config.codex.model_routing))


# ---------------------------------------------------------------------------
# Single agent — read all files
# ---------------------------------------------------------------------------


@router.get("/{agent_name:path}", response_model=AgentDetailResponse)
def get_agent(agent_name: str):
    """Return all file contents for a single agent."""
    try:
        files = _store().get_agent(agent_name)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentDetailResponse(
        name=agent_name,
        files=files,
        is_builtin=agent_name.split("/")[-1] in {"module_maker", "prompt_generator", "code_generator", "code_reviewer"},
    )


# ---------------------------------------------------------------------------
# Single agent file — read / write
# ---------------------------------------------------------------------------


@router.get("/{agent_name:path}/{file_name}", response_model=AgentFileResponse)
def get_agent_file(agent_name: str, file_name: str):
    """Return the content of a single .md file for an agent."""
    if file_name not in AGENT_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file '{file_name}'. Valid files: {list(AGENT_FILES)}",
        )
    try:
        content = _store().get_file(agent_name, file_name)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AgentFileResponse(agent_name=agent_name, file_name=file_name, content=content)


@router.put("/{agent_name:path}/{file_name}", response_model=AgentFileResponse)
def update_agent_file(
    agent_name: str,
    file_name: str,
    body: UpdateAgentFileRequest,
    orch=Depends(get_orchestrator),
):
    """Overwrite a single .md file for an agent — writes to disk AND database."""
    if file_name not in AGENT_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file '{file_name}'. Valid files: {list(AGENT_FILES)}",
        )
    try:
        _store().update_file(agent_name, file_name, body.content)
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Mirror to DB
    _cfg_repo(orch).upsert_file(agent_name, file_name, body.content)
    return AgentFileResponse(agent_name=agent_name, file_name=file_name, content=body.content)


# ---------------------------------------------------------------------------
# Custom agent lifecycle
# ---------------------------------------------------------------------------


@router.post("", response_model=AgentDetailResponse, status_code=201)
def create_agent(body: CreateAgentRequest):
    """Create a new custom agent with optional initial file contents."""
    try:
        _store().create_agent(body.name, body.files or {})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    files = _store().get_agent(f"custom/{body.name}")
    return AgentDetailResponse(name=f"custom/{body.name}", files=files, is_builtin=False)


@router.delete("/{agent_name:path}", status_code=204)
def delete_agent(agent_name: str, orch=Depends(get_orchestrator)):
    """Delete a custom agent. Built-in agents cannot be deleted."""
    try:
        _store().delete_agent(agent_name)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AgentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Remove mirrored DB entries so the agent doesn't reappear on reload
    try:
        _cfg_repo(orch).delete_agent_files(agent_name)
    except Exception as exc:  # non-fatal: filesystem delete already succeeded
        logger.warning("Could not remove agent_files DB entries for '%s': %s", agent_name, exc)

