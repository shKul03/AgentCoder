# Prompt Generator — Tools

## Available Tools

### 1. OpenAI Chat Completions API
**What it does:** Calls `openai.chat.completions.create` (or `AsyncOpenAI` equivalent) with a carefully constructed system prompt and user message to generate the implementation or fix prompt.
**Input:** System prompt (role/style instructions), user message (raw requirements or review JSON), model name, API key from `config.secrets.openai_api_key`
**Output:** Streamed text response — the generated prompt
**Used for:** All prompt generation. This is the primary and only LLM tool.

### 2. Prompt File Writer
**What it does:** Writes the fully generated prompt string to the configured fixed file path on disk.
**Input:** Prompt string, target file path (`config.project.prompt_file_path` or `data/prompts/latest.md`)
**Output:** Path of the written file
**Used for:** Persisting the prompt for HITL review and Code Generator consumption

### 3. Review JSON Reader
**What it does:** Reads the structured review JSON file from the path produced by the Code Reviewer in the previous iteration.
**Input:** Review JSON file path (passed from orchestrator)
**Output:** Parsed review JSON (overall_status, checklist_scores, line_comments, global_comments, summary)
**Used for:** Supplying the fix context to the OpenAI API call in iteration 2+

### 4. Requirements Reader
**What it does:** Reads the raw requirements text from the configured source (local file, JIRA, Asana, or ADO — already fetched and cached by the orchestrator).
**Input:** Requirements file path or pre-loaded text string
**Output:** Requirements string
**Used for:** Supplying the source material to the OpenAI API call in iteration 1
