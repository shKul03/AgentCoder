# Orchestrator — Tools

## Available Tools

### 1. Requirements Loader
**What it does:** Reads requirements from the configured source and returns the requirements text.
- Device: `open(config.requirements.path).read()`
- JIRA: HTTP call to `config.requirements.jira_url` with API token authentication
- Asana: HTTP call to Asana API with `config.requirements.asana_token`
- ADO: HTTP call to ADO Work Items API with `config.requirements.ado_token`
**Output:** Requirements text string
**Used for:** Providing source material to the Prompt Generator in iteration 1

### 2. Pipeline State Store
**What it does:** Reads and writes the current pipeline state (`PipelineState`) to persistent storage (SQLite via `storage/pipeline_state.py`).
**Fields tracked:** `pipeline_status`, `current_iteration`, `prompt_path`, `review_json_path`, `cli_tool_used`, `last_updated`
**Used for:** Surviving server restarts, broadcasting state to the frontend via WebSocket

### 3. WebSocket Broadcaster
**What it does:** Broadcasts pipeline state change events to all connected frontend WebSocket clients.
**Input:** Event type string, event data dict
**Used for:** Real-time UI updates (status bar, iteration counter, pipeline diagram)

### 4. REST API Router
**What it does:** Exposes the orchestrator's control surface as FastAPI routes:
- `GET /api/orchestrator/status`
- `POST /api/orchestrator/start`
- `POST /api/orchestrator/approve-prompt`
- `POST /api/orchestrator/approve-review`
- `POST /api/orchestrator/pause`
- `POST /api/orchestrator/reset`
- `GET /api/orchestrator/iterations`
- `GET /api/orchestrator/current-prompt`
- `GET /api/orchestrator/current-review`
**Used for:** All frontend-to-backend communication

### 5. VCS Client Factory
**What it does:** Reads `config.requirements.source` and returns the appropriate `VCSClient` instance.
- `source == "ado"` → `ADOClient(org, project, token)`
- all others → `GitHubClient(owner, token)`
**Used for:** Injecting the correct VCS client into Code Generator and Code Reviewer runners without those runners knowing which provider is active
