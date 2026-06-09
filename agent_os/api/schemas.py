"""Response schemas for the Agent OS API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class OrchestratorStatusResponse(BaseModel):
    pipeline_status: str
    current_iteration: int = 0
    last_checkpoint: datetime
    metadata: dict[str, Any] = {}
    is_hitl_gate: bool = False
    # ── GitHub Review mode fields (Phase 8) ──────────────────────────────────
    mode: str = "standard"
    current_story_id: Optional[str] = None
    stories_completed: int = 0
    stories_total: int = 0


# Backward-compat alias
PipelineStatusResponse = OrchestratorStatusResponse


class IterationResponse(BaseModel):
    id: Optional[int] = None
    iteration_number: int
    status: str
    prompt_path: str = ""
    prompt_content: str = ""
    review_json_path: str = ""
    review_json_content: str = ""
    token_usage: int = 0
    cli_tool_used: str = ""
    ci_result: str = ""
    ci_output: str = ""
    started_at: datetime
    completed_at: Optional[datetime] = None


class IterationListResponse(BaseModel):
    iterations: list[IterationResponse] = []


class CurrentPromptResponse(BaseModel):
    iteration: int = 0
    content: str = ""
    path: str = ""


class CurrentReviewResponse(BaseModel):
    iteration: int = 0
    content: str = ""
    path: str = ""


class ApprovePromptRequest(BaseModel):
    prompt_content: Optional[str] = None
    cli_tool: Optional[str] = None
    cli_model: Optional[str] = None


class RequirementResponse(BaseModel):
    id: str
    type: str
    parent_id: Optional[str] = None
    title: str
    description: str = ""
    status: str = "active"


class ApproveGateRequest(BaseModel):
    gate: Optional[str] = None


class StoryQueueDetailResponse(BaseModel):
    """Single story-queue item returned by GET /story-queue/{story_id}."""

    story_id: str
    title: str
    description: str = ""
    acceptance_criteria: list[str] = []
    position: int = 0
    status: str = "queued"
    branch_name: str = ""
    pr_number: Optional[int] = None
    pr_url: str = ""
    story_iteration: int = 0
    depends_on: list[str] = []
    dependency_reason: str = ""
    created_at: str = ""
    completed_at: Optional[str] = None


class StoryQueueReorderRequest(BaseModel):
    """Body for POST /story-queue/reorder."""

    story_ids: list[str]  # desired order, first element = position 0


class ApproveGateResponse(BaseModel):
    approved: bool
    message: str


class BusMessageResponse(BaseModel):
    channel: str
    sender: str
    timestamp: datetime
    module_id: Optional[str] = None
    iteration: int = 0
    correlation_id: str = ""
    payload: dict[str, Any] = {}


class MetricsResponse(BaseModel):
    total_iterations: int = 0
    total_token_usage: int = 0
    pipeline_status: str = "idle"
    total_cost: float = 0.0


class ModuleBudgetResponse(BaseModel):
    module_id: str
    tokens_used: int = 0
    token_budget: int = 0
    usage_pct: float = 0.0
    cost: float = 0.0
    status: str = "ok"


# ── Settings schemas ──────────────────────────────────────────────


class SecretsSettingsResponse(BaseModel):
    openai_api_key: str = ""
    github_token: str = ""


class GitHubSettingsResponse(BaseModel):
    owner: str = ""
    repo: str = ""
    auto_push: bool = False
    auto_create_pr: bool = False


class ProjectSettingsResponse(BaseModel):
    name: str = ""
    root_path: str = ""
    language: str = "python"
    repo_name: str = ""
    feature_branch: str = "dev"
    prompt_file_path: str = ""


class PipelineSettingsResponse(BaseModel):
    max_iterations: int = 5
    convergence_rule: str = "no_high_severity"
    auto_approve_hitl: bool = False


class CliRoutingSettingsResponse(BaseModel):
    """Per-agent CLI tool selection (e.g. codex, aider, claude)."""
    PROMPT_GENERATOR: str = "codex"
    CODE_GENERATOR: str = "codex"
    CODE_REVIEWER: str = "codex"


class RequirementsSettingsResponse(BaseModel):
    """Requirements ingestion configuration."""
    path: str = "requirements.yaml"
    source: str = "device"          # "device" | "jira" | "asana" | "ado"
    # JIRA
    jira_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""
    # Asana
    asana_token: str = ""
    asana_project_id: str = ""
    # Azure DevOps
    ado_org: str = ""
    ado_token: str = ""
    ado_project: str = ""


class GitHubReviewSettingsResponse(BaseModel):
    """GitHub Review mode configuration (Phase 2 Mode C)."""
    source_repo_url: str = ""
    requirements_path: str = ""
    fork_repo_name: str = ""
    branch_name: str = "agent-os-fixes"


class AIToolCredentialResponse(BaseModel):
    """Serialisable form of a single AI tool's auth config (API key masked)."""
    enabled: bool = False
    auth_method: str = ""
    api_key: str = ""        # masked on GET ("***" when set)
    email: str = ""
    account_id: str = ""
    endpoint: str = ""
    extra: dict = {}


class AIToolsSettingsResponse(BaseModel):
    codex: AIToolCredentialResponse = AIToolCredentialResponse()
    claude: AIToolCredentialResponse = AIToolCredentialResponse()
    gemini: AIToolCredentialResponse = AIToolCredentialResponse()
    qwen: AIToolCredentialResponse = AIToolCredentialResponse()
    deepseek: AIToolCredentialResponse = AIToolCredentialResponse()
    cursor: AIToolCredentialResponse = AIToolCredentialResponse()
    copilot: AIToolCredentialResponse = AIToolCredentialResponse()


class VCSSettingsResponse(BaseModel):
    """VCS target provider — independent of requirements source."""
    provider: str = "github"  # "github" | "ado"


class OllamaSettingsResponse(BaseModel):
    """Ollama service connection settings."""
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    timeout_seconds: int = 300


class PromptGeneratorSettingsResponse(BaseModel):
    """Prompt generator LLM provider selection."""
    provider: str = "ollama"       # "ollama" | "openai"
    ollama_model: str = "llama3.1:8b"
    openai_model: str = "gpt-4.1-mini"


class CodeReviewerSettingsResponse(BaseModel):
    """Code reviewer LLM provider selection."""
    provider: str = "openai"       # "openai" | "copilot" | "ollama"
    model: str = "gpt-4.1-mini"   # used for openai and copilot
    ollama_model: str = "llama3.1:8b"


class SettingsResponse(BaseModel):
    secrets: SecretsSettingsResponse = SecretsSettingsResponse()
    github: GitHubSettingsResponse = GitHubSettingsResponse()
    project: ProjectSettingsResponse = ProjectSettingsResponse()
    pipeline: PipelineSettingsResponse = PipelineSettingsResponse()
    cli_routing: CliRoutingSettingsResponse = CliRoutingSettingsResponse()
    requirements: RequirementsSettingsResponse = RequirementsSettingsResponse()
    github_review: GitHubReviewSettingsResponse = GitHubReviewSettingsResponse()
    pipeline_mode: str = "standard"
    ai_tools: AIToolsSettingsResponse = AIToolsSettingsResponse()
    vcs: VCSSettingsResponse = VCSSettingsResponse()
    ollama: OllamaSettingsResponse = OllamaSettingsResponse()
    prompt_generator: PromptGeneratorSettingsResponse = PromptGeneratorSettingsResponse()
    code_reviewer: CodeReviewerSettingsResponse = CodeReviewerSettingsResponse()


class SettingsUpdateRequest(BaseModel):
    secrets: Optional[SecretsSettingsResponse] = None
    github: Optional[GitHubSettingsResponse] = None
    project: Optional[ProjectSettingsResponse] = None
    pipeline: Optional[PipelineSettingsResponse] = None
    cli_routing: Optional[CliRoutingSettingsResponse] = None
    requirements: Optional[RequirementsSettingsResponse] = None
    github_review: Optional[GitHubReviewSettingsResponse] = None
    pipeline_mode: Optional[str] = None
    ai_tools: Optional[AIToolsSettingsResponse] = None
    vcs: Optional[VCSSettingsResponse] = None
    ollama: Optional[OllamaSettingsResponse] = None
    prompt_generator: Optional[PromptGeneratorSettingsResponse] = None
    code_reviewer: Optional[CodeReviewerSettingsResponse] = None


class TestGitHubRequest(BaseModel):
    """Optional body for test-github — pass token to test an unsaved value."""
    token: str = ""


class TestGitHubResponse(BaseModel):
    valid: bool
    user: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Agent Identity schemas
# ---------------------------------------------------------------------------


class AgentMeta(BaseModel):
    name: str
    display_name: str
    is_builtin: bool
    is_custom: bool
    files_present: list[str]
    post_assignment: Optional[str] = None


class AgentListResponse(BaseModel):
    agents: list[AgentMeta]


class AgentDetailResponse(BaseModel):
    name: str
    is_builtin: bool
    files: dict[str, str]  # {filename: content}


class AgentFileResponse(BaseModel):
    agent_name: str
    file_name: str
    content: str


class AgentRegistryResponse(BaseModel):
    mapping: dict[str, str]  # {PIPELINE_POST: agent_name}


class CreateAgentRequest(BaseModel):
    name: str
    files: Optional[dict[str, str]] = None  # {filename: initial_content}


class UpdateAgentFileRequest(BaseModel):
    content: str


class UpdateRegistryRequest(BaseModel):
    mapping: dict[str, str]  # {PIPELINE_POST: agent_name}
