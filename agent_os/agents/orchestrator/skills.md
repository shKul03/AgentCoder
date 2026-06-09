# Orchestrator — Skills

## Core Capabilities

1. **Requirements Ingestion** — Loads requirements from the configured source:
   - Device: reads a local file (`requirements.yaml`, `.txt`, `.csv`, `.xlsx`)
   - JIRA: fetches issues from the configured JIRA project via API
   - Asana: fetches tasks from the configured Asana project via API
   - Azure DevOps: fetches work items from the configured ADO project via API
   Stores the loaded requirements text for use by the Prompt Generator.

2. **Prompt Generator Routing** — Calls the Prompt Generator with the appropriate input:
   - Iteration 1: passes the loaded requirements text
   - Iteration 2+: passes the review JSON path from the previous iteration
   Waits for streaming completion and stores the generated prompt path.

3. **HITL Checkpoint 1 — Prompt Review** — Transitions to `HITL_PROMPT_REVIEW` state.
   Surfaces the generated prompt for human review and editing in the UI.
   Waits for `POST /api/orchestrator/approve-prompt` with optional edited prompt and selected CLI tool.
   On approval, passes the (possibly edited) prompt and CLI tool selection to the Code Generator.

4. **Code Generator Routing** — Calls the Code Generator with the approved prompt and selected CLI tool.
   Tracks whether this is iteration 1 (full generation + repo creation + main push + PR creation) or iteration 2+ (fixes + comment resolution + feature branch push).
   Waits for Code Generator completion before proceeding.

5. **Code Reviewer Routing** — Calls the Code Reviewer with the PR details from the Code Generator step.
   Waits for the review JSON to be written before proceeding.

6. **HITL Checkpoint 2 — Review Decision** — Transitions to `HITL_REVIEW_DECISION` state.
   Surfaces the review JSON for human inspection in the UI.
   Waits for `POST /api/orchestrator/approve-review`.
   On approval, checks whether review status is `accepted` (→ `PIPELINE_COMPLETE`) or `needs_work` / `rejected` (→ loops back to Prompt Generation).

7. **Iteration Tracking** — Increments `current_iteration` after each completed cycle.
   Enforces `max_iterations` limit: if reached, transitions to `PIPELINE_COMPLETE` regardless of review status.

8. **Pipeline State Management** — Persists and broadcasts pipeline state changes via WebSocket to all connected frontend clients.

9. **VCS Client Initialisation** — Reads `config.requirements.source` and passes the correct `VCSClient` instance (`GitHubClient` or `ADOClient`) to the Code Generator and Code Reviewer runners.
