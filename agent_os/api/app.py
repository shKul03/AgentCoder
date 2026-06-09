"""FastAPI application factory for Agent OS dashboard API."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config.loader import get_default_config, load_config
from ..config.env import auto_load_dotenv
from .deps import orch_holder
from .routes import agents, cli_tools, metrics, orchestrator, project, requirements, settings
from .websocket import _broadcast_worker, _setup_bus_subscriptions, router as ws_router, _queue as _ws_queue

logger = logging.getLogger(__name__)

# Module-level config path override (set before app starts if needed)
_config_path: str | None = None


def _sync_project_config(config, config_path) -> None:
    """Populate project.name and project.root_path from requirements.yaml on startup."""
    from pathlib import Path as _Path
    from ..utils.requirements_name import extract_project_name, name_to_slug

    try:
        req_path = _Path(config.requirements.path)
        if req_path.exists():
            name = extract_project_name(str(req_path))
            if name and name != "Agent OS Project":
                config.project.name = name
                slug = name_to_slug(name)
                if not config.project.repo_name or config.project.repo_name.lower().startswith("imported"):
                    config.project.repo_name = slug
                logger.info("Project name set from requirements: %s", name)

        # Persist any changes back to config.yaml
        if config_path:
            from .routes.settings import _write_config_yaml
            _write_config_yaml(config, config_path)
    except Exception:
        logger.debug("Could not sync project config on startup (non-fatal)", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown lifecycle for the API server."""
    # Auto-load .env from the Agent OS root (where config.yaml lives) into
    # os.environ before anything else, so secrets are available process-wide.
    _env_root = str(Path(_config_path).resolve().parent) if _config_path else "."
    _auto_yaml = Path("config.yaml").resolve()
    if not _config_path and _auto_yaml.exists():
        _env_root = str(_auto_yaml.parent)
    auto_load_dotenv(_env_root)

    if _config_path:
        config = load_config(_config_path)
        orch_holder.config_path = Path(_config_path).resolve()
    else:
        # Auto-discover config.yaml in the current working directory so that
        # `uvicorn agent_os.api.app:app` picks up config.yaml automatically
        # and _write_config_yaml can persist project.name / root_path back.
        _auto_config = Path("config.yaml").resolve()
        if _auto_config.exists():
            config = load_config(_auto_config)
            orch_holder.config_path = _auto_config
        else:
            config = get_default_config()
            orch_holder.config_path = None
    orch_holder.init(config)
    logger.info("Orchestrator initialised")

    # Reconcile model_routing: the DB is the authoritative store for UI changes
    # made during a previous session.  If the DB holds a routing that differs
    # from what config.yaml has, update the live config so the runner always
    # uses the same model the frontend shows in the dropdown.
    try:
        from ..storage.agent_config_repo import AgentConfigRepo
        _orch = orch_holder.orchestrator
        db_routing = AgentConfigRepo(_orch.db.conn).get_model_routing()
        if db_routing:
            _orch.config.codex.model_routing.update(db_routing)
            logger.info("Startup: merged DB model_routing into live config: %s", db_routing)
    except Exception:
        logger.debug("Could not merge DB model_routing at startup (non-fatal)", exc_info=True)

    # Wire the shared asyncio broadcast queue into the orchestrator so that
    # every _emit() call from the pipeline loop reaches WebSocket clients.
    orch_holder.orchestrator.set_ws_queue(_ws_queue)

    # Proactively sync project name and folder from requirements.yaml so the
    # dashboard shows correct info even before the pipeline is started.
    _sync_project_config(config, orch_holder.config_path)

    # Subscribe CommBus → WebSocket bridge
    _setup_bus_subscriptions()

    # Start background task that drains the WS broadcast queue
    task = asyncio.create_task(_broadcast_worker())

    yield

    task.cancel()
    orch_holder.shutdown()
    logger.info("Orchestrator shut down")


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="Agent OS",
        description="Autonomous SDLC Pipeline — Dashboard API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow the React dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST routers
    app.include_router(requirements.router)
    app.include_router(metrics.router)
    app.include_router(settings.router)
    app.include_router(project.router)
    app.include_router(agents.router)
    app.include_router(orchestrator.router)
    app.include_router(cli_tools.router)

    # WebSocket router
    app.include_router(ws_router)

    # Serve React build if it exists
    static_dir = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="frontend")

    return app


# Default app instance for `uvicorn agent_os.api.app:app`
app = create_app()
