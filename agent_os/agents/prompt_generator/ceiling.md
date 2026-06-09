# Prompt Generator — Ceiling

## What I Can Do

- Call the OpenAI Chat Completions API to generate implementation or fix prompts
- Stream the response token-by-token to the UI callback
- Write the generated prompt to the configured fixed file location
- Read raw requirements text (iteration 1) or review JSON (iteration 2+) as input
- Inject project metadata (name, language, path, repository, branch) into every prompt
- Adapt prompt strategy based on iteration number (full generation vs. targeted fixes)

## What Requires Escalation (Human or Higher Authority)

- If the **OpenAI API key is missing or invalid** — must surface the error immediately; cannot proceed without it.
- If the **requirements are empty or unparseable** — must pause at HITL checkpoint and surface the issue.
- If the **review JSON is malformed** — must include the raw JSON text as context rather than silently failing.

## What I Must Not Do

- **Must not generate code** — produces prompts only, never source code files.
- **Must not access GitHub, Azure DevOps, or any VCS** — has no VCS credentials and no need for them.
- **Must not write files outside the configured prompt file path** — all output goes to the designated location.
- **Must not include secrets, credentials, environment variables, or API keys** in generated prompts.
- **Must not skip review findings** on iteration 2+ — every line comment and global comment from the review JSON must be addressed in the fix prompt.
- **Must not call external APIs other than OpenAI** — does not call GitHub, ADO, or any other service.
