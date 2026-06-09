# Code Generator — Tools

## Available Tools

### 1. File System — Read
**What it does:** Reads the contents of any file within the project root.
**Input:** Absolute or project-relative file path
**Output:** File contents as string
**Used for:** Reading existing code to understand conventions, reading config files, reading the prompt file

### 2. File System — Write
**What it does:** Creates a new file or overwrites an existing file at a specified path within the project root.
**Input:** File path, file contents
**Output:** Confirmation of write
**Used for:** Writing all generated source files, test files, CI script, and `summary.md`

### 3. File System — Create Directory
**What it does:** Creates a directory (and all parents) at the specified path.
**Input:** Directory path
**Output:** Confirmation of creation
**Used for:** Setting up the project's folder structure before writing files

### 4. Git CLI
**What it does:** Executes git commands (`git init`, `git add`, `git commit`, `git checkout -b`, `git push`) in the project root directory.
**Input:** Git command string, working directory
**Output:** stdout, stderr, exit code
**Used for:** All local git operations — initialising the repo, staging, committing, and pushing to remote

### 5. GitHub API (GitHubClient)
**What it does:** Makes authenticated HTTP calls to `api.github.com` using the GitHub PAT.
**Operations:** `create_repo`, `get_remote_url`, `create_pr`, `resolve_comment`, `delete_branch`
**Input:** Repo name, branch names, PR details, comment IDs
**Used for:** All GitHub VCS operations when `requirements_source != "ado"`

### 6. Azure DevOps API (ADOClient)
**What it does:** Makes authenticated HTTP calls to `dev.azure.com/{org}/{project}/_apis/git/` using ADO PAT encoded as `Basic {base64(":pat")}`.
**Operations:** `create_repo`, `get_remote_url`, `create_pr`, `resolve_thread`, `delete_branch`
**Input:** Repo name, branch names, PR details, thread IDs
**Used for:** All ADO VCS operations when `requirements_source == "ado"`

### 7. CI Pipeline Runner
**What it does:** Executes the project-root `ci_check.py` script as a subprocess before every push.
**Input:** Project root path, timeout
**Output:** CI pass/fail result, stdout/stderr
**Used for:** Validating the build succeeds before pushing; blocks push on CI failure

### 8. Summary Writer
**What it does:** Writes the `summary.md` completion signal file to the working directory.
**Input:** Summary text describing what was implemented and pushed
**Output:** `summary.md` file
**Used for:** Signaling successful completion to the pipeline
