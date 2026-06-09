# Code Reviewer — Tools

## Available Tools

### 1. GitHub API (GitHubClient)
**What it does:** Makes authenticated HTTP calls to `api.github.com` using the GitHub PAT.
**Operations used:**
- `GET /repos/{owner}/{repo}/pulls/{pr}` (with `Accept: application/vnd.github.diff`) — fetch PR diff
- `POST /repos/{owner}/{repo}/pulls/{pr}/comments` (with `path` + `line`) — add inline comment
- `POST /repos/{owner}/{repo}/issues/{pr}/comments` — add global PR comment
- `POST /repos/{owner}/{repo}/pulls/{pr}/reviews` — submit approval
- `PUT /repos/{owner}/{repo}/pulls/{pr}/merge` — merge PR
- `DELETE /repos/{owner}/{repo}/git/refs/heads/{branch}` — delete feature branch
**Used for:** All review operations when `requirements_source != "ado"`

### 2. Azure DevOps API (ADOClient)
**What it does:** Makes authenticated HTTP calls to `dev.azure.com/{org}/{project}/_apis/git/` using ADO PAT encoded as `Basic {base64(":pat")}`.
**Operations used:**
- `GET .../pullrequests/{prId}/iterations/{iterationId}/changes` — fetch PR diff
- `POST .../pullrequests/{prId}/threads` (with `threadContext` for file/line) — add inline comment
- `POST .../pullrequests/{prId}/threads` (without `threadContext`) — add global comment
- `PATCH .../pullrequests/{prId}` with `{"status": "completed"}` — merge PR
- `DELETE .../refs` with `newObjectId` all zeros + current tip SHA — delete feature branch
**Used for:** All review operations when `requirements_source == "ado"`

### 3. Review JSON Writer
**What it does:** Writes the structured review JSON to the configured file path.
**Input:** Review JSON object, iteration number, output directory
**Output:** Persisted review JSON file
**Used for:** Making the review durable for orchestrator, HITL display, and Prompt Generator consumption

**Note: There is NO local file system reader.** The Code Reviewer does not read any local source files. All code access is exclusively via VCS API PR diff.
