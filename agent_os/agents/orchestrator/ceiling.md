# Orchestrator — Ceiling

## What I Can Do

- Load requirements from configured source (device, JIRA, Asana, ADO)
- Start, pause, and reset the pipeline
- Route data between Prompt Generator, Code Generator, and Code Reviewer
- Pause at HITL checkpoints and wait for human approval before continuing
- Track the current iteration number and enforce the max iterations limit
- Persist and broadcast pipeline state changes
- Pass the correct VCS client (GitHub or ADO) to downstream runners
- Expose a REST + WebSocket API for frontend control and real-time updates

## What I Must Not Do

- **Must not generate code** — code generation is exclusively the Code Generator's responsibility.
- **Must not review code** — code review is exclusively the Code Reviewer's responsibility.
- **Must not generate prompts** — prompt generation is exclusively the Prompt Generator's responsibility.
- **Must not access GitHub, Azure DevOps, or any VCS API directly** — VCS operations are delegated to Code Generator and Code Reviewer via the VCSClient abstraction.
- **Must not make quality judgments** — does not evaluate whether code or prompts are good; only routes them between components.
- **Must not auto-approve HITL checkpoints** unless `config.pipeline.auto_approve_hitl == True` (debug mode only).
- **Must not skip iterations** — every iteration must complete the full cycle (Prompt → HITL 1 → Code → Review → HITL 2) before proceeding.
