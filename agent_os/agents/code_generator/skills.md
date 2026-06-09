# Code Generator — Skills

## Core Capabilities

### Iteration 1 — Initial Generation
1. **Full codebase generation** — Generates the entire project from the approved prompt: all source files, tests, configuration, README, and dependency manifests.
2. **CI pipeline script creation** — Generates `ci_check.py` (or equivalent) at the project root. The script validates the build succeeds (e.g. `python -m py_compile`, `npm run build`, unit tests) before allowing any push. This file MUST NOT be in `.gitignore`.
3. **Git repository initialisation** — Initialises a local git repo, sets the remote origin using the VCS provider URL, and creates the initial commit.
4. **Push to `main` branch** — Pushes the initial codebase to `main`. This is the ONLY iteration where a push to `main` is made directly by the Code Generator.
5. **Push to feature branch** — Also pushes to the configurable feature branch (default: `dev`).
6. **Pull Request creation** — Creates a Pull Request from the feature branch to `main` via the VCS provider API.

### Iteration 2+ — Fix Iterations
1. **Targeted code corrections** — Applies the fixes specified in the approved fix prompt. Makes only the changes called for; does not rewrite unrelated code.
2. **PR comment resolution** — Resolves every outstanding PR comment added by the Code Reviewer:   - GitHub: marks each review comment thread as resolved via the GitHub API.   - ADO: `PATCH .../threads/{id}` with `{"status": "fixed"}` via the ADO API.
3. **CI pipeline execution** — Runs `ci_check.py` before every push. If CI fails, fixes the issue before pushing.
4. **Feature branch push only** — Pushes corrected code to the feature branch. Never pushes to `main` in iteration 2+.

### VCS Provider Selection
- Reads `config.requirements.source` at runtime.
- If `source == "ado"` → uses `ADOClient` for all git/PR/comment operations.
- All other sources → uses `GitHubClient`.
- Does not branch on provider type in runner logic — all operations go through the `VCSClient` abstraction.
