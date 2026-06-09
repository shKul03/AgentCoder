# Prompt Generator — Skills

## Core Capabilities

1. **Implementation Prompt Generation (Iteration 1)** — Calls the OpenAI Chat Completions API directly with the raw requirements text to produce a complete, richly-detailed implementation prompt. The prompt covers project structure, technology stack, all required files, API design, data models, and a Definition of Done.

2. **Fix Prompt Generation (Iteration 2+)** — Given the structured review JSON from the Code Reviewer, calls the OpenAI API to generate a targeted fixes-only prompt from scratch. The prompt maps every review finding (line comments, global comments, checklist failures) to specific corrective actions. It is a complete standalone prompt, not an incremental patch.

3. **Streaming Output** — Streams the OpenAI API response token-by-token to a callback so the UI can display generation in real time.

4. **Prompt File Persistence** — Writes the final generated prompt to the configured fixed file path (`config.project.prompt_file_path`, fallback `data/prompts/latest.md`) for HITL review and Code Generator consumption.

5. **Context Injection** — Injects project metadata (name, language, root path, target repository, feature branch) into every prompt so the Code Generator always has full situational awareness.

6. **Iteration Awareness** — Detects whether the current run is iteration 1 (requirements available, no review JSON) or iteration 2+ (review JSON available), and selects the appropriate prompt strategy automatically.
