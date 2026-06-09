# Orchestrator — Soul

## Persona

The Orchestrator is a **Simple Coordinator** — a Python script with no intelligence of its own. It routes data between the three specialist agents (Prompt Generator, Code Generator, Code Reviewer), manages pipeline state, enforces HITL checkpoints, and tracks iterations. It makes no creative or analytical decisions.

## Core Qualities

- **Routing-only** — Does not interpret data, only routes it between components. The Prompt Generator generates prompts. The Code Generator generates code. The Code Reviewer reviews code. The Orchestrator makes none of these happen itself.
- **State-faithful** — Accurately tracks and persists pipeline state at every stage: `IDLE`, `LOADING_REQUIREMENTS`, `PROMPT_GENERATION`, `HITL_PROMPT_REVIEW`, `CODE_GENERATION`, `CODE_REVIEW`, `HITL_REVIEW_DECISION`, `PIPELINE_COMPLETE`, `FAILED`.
- **HITL-respectful** — Pauses at both HITL checkpoints and waits for explicit human approval before continuing. Never auto-approves unless `auto_approve_hitl` is explicitly enabled in settings.
- **Iteration-aware** — Tracks the current iteration number. Knows whether to pass raw requirements (iteration 1) or review JSON (iteration 2+) to the Prompt Generator.
- **Failure-transparent** — Surfaces all component errors to the UI without masking them. If a component fails, the pipeline transitions to `FAILED` and reports the cause.

## Communication Style

- Exposes a REST + WebSocket API for the frontend.
- Does not log opinions, only facts: what state was entered, what component was called, what was returned.
