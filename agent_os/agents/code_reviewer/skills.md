# Code Reviewer — Skills

## Core Capabilities

1. **VCS PR Diff Review** — Fetches the complete PR diff from the configured VCS provider (GitHub or ADO). All code analysis is performed on this diff — never on local files.

2. **Full 15-Point Checklist Review** — ALL criteria are run on every review iteration. None can be skipped:
   - **Code Correctness** — logic errors, bugs, edge cases, unexpected behaviour
   - **Readability & Clarity** — naming conventions, code formatting, inline comments
   - **Code Structure & Design** — modularity, SOLID principles, separation of concerns
   - **Performance & Efficiency** — algorithmic complexity, unnecessary loops, memory usage
   - **Security** — injection vulnerabilities, auth weaknesses, data exposure, OWASP Top 10
   - **Error Handling** — exception coverage, fallback logic, graceful degradation
   - **Code Standards** — style guide conformance, linting, formatting consistency
   - **Testing & Coverage** — unit/integration test presence, test quality, coverage percentage
   - **Documentation** — docstrings, README accuracy, inline comments for complex logic
   - **Maintainability** — tech debt, code duplication, excessive coupling, fragility
   - **Dependencies & Imports** — unused imports, outdated dependencies, version pinning
   - **Logging & Monitoring** — structured logging, error tracing, observability
   - **Version Control** — commit message quality, branch hygiene, merge readiness
   - **UI/UX** — usability, accessibility, responsive design (only when frontend files changed)
   - **Overall Impact** — risk assessment, regression potential, release readiness

3. **Additional Architecture Checks** (on top of the 15-point checklist):
   - **File size rule** — flags every file exceeding 200 lines in the PR diff
   - **Clean architecture compliance** — verifies layer separation (no business logic in routes, no DB calls from UI layer, no cross-layer bypasses)
   - **Folder structure review** — verifies every new/modified file is in the correct project directory per declared conventions

4. **Strict Rejection Rules** (unchanged):
   - Syntax error present → **Rejected** immediately, no score assigned
   - Tests failing → convergence score capped at ≤ 40
   - Critical security vulnerability present → **Rejected** immediately

5. **Inline PR Comments** — Posts line-specific comments on PR files for code correctness, readability, security, and file-size violations via the VCS provider API.

6. **Global PR Comments** — Posts overall comments on the PR for structural and architectural findings.

7. **Structured Review JSON Generation** — Writes review JSON aggregating all findings:
   - `overall_status`: `"needs_work"` | `"accepted"` | `"rejected"`
   - `checklist_scores`: dict mapping each of the 15 criteria to a score 0–100
   - `overall_score`: int 0–100 (weighted average)
   - `line_comments`: list of `{file, line, comment, severity, checklist_item}`
   - `global_comments`: list of `{comment, category, severity}`
   - `file_size_violations`: list of `{file, line_count}` for files > 200 lines
   - `architecture_issues`: list of `{description, layer, severity}`
   - `folder_structure_issues`: list of `{path, issue, expected_location}`
   - `summary`: overall verdict and recommended next steps

8. **Final Iteration Operations** — When `overall_status == "accepted"` and no open issues:
   - Approves the PR via VCS API
   - Merges the PR to `main`
   - Deletes the feature branch via VCS API

9. **VCS Provider Selection** — Same runtime conditional as code_generator: `requirements_source == "ado"` → ADOClient; otherwise GitHubClient.

2. **Acceptance Criteria Verification** — Checks every acceptance criterion from the original requirements against the generated code, producing a `passed/failed` verdict with evidence for each AC.

3. **Multi-Dimensional Scoring** — Scores code quality across four dimensions: Design (architecture, patterns, coupling), Security (OWASP Top 10, input validation, secrets), Testing (coverage, meaningful assertions, edge cases), and Performance (N+1 queries, unnecessary computation, blocking I/O).

4. **Validation Result Interpretation** — Reads lint, type-check, test, and security scan outputs from the Validation step and incorporates them into the review, preventing the same issues from being independently re-identified.

5. **Convergence Score Assessment** — Produces a `convergence_score` (0–100) representing how close the code is to being production-ready, guiding the Decision Engine on whether to accept or iterate.

6. **Issue Severity Classification** — Classifies every identified issue by severity (critical, high, medium, low, info) and category (bug, security, performance, design, style, testing, documentation, other), enabling the convergence rule to make a data-driven accept/iterate decision.

7. **Targeted Fix Guidance** — For every issue, provides a `suggested_fix` that is specific enough for the Prompt Generator to incorporate directly into the next iteration's prompt.
