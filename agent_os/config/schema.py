"""Configuration schema — Pydantic models for Agent OS config."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class PromptFramework(str, Enum):
    RCTCF = "RCTCF"
    RISEN = "RISEN"
    COSTAR = "COSTAR"
    CUSTOM = "CUSTOM"


class ConvergenceRule(str, Enum):
    NO_HIGH_SEVERITY = "no_high_severity"
    NO_CRITICAL = "no_critical"
    ALL_ACCEPTED = "all_accepted"


class ProjectConfig(BaseModel):
    name: str = ""
    root_path: str = ""
    language: str = "python"
    repo_name: str = ""
    feature_branch: str = "dev"
    prompt_file_path: str = ""
    review_json_path: str = ""   # where code reviewer writes the review JSON


class OrchestratorConfig(BaseModel):
    max_iterations: int = Field(default=5, ge=1, le=20)
    auto_approve_hitl: bool = False
    hitl_timeout_seconds: int = Field(default=0, ge=0)
    convergence_rule: ConvergenceRule = ConvergenceRule.NO_HIGH_SEVERITY


class CodexConfig(BaseModel):
    model: str = "codex"
    timeout_seconds: int = Field(default=1200, ge=30)
    max_retries: int = Field(default=2, ge=0, le=5)
    model_routing: dict[str, str] = Field(default_factory=lambda: {
        "PROMPT_GENERATOR": "gpt-4.1-mini",
        "CODE_GENERATOR": "gpt-4.1",
        "CODE_REVIEWER": "gpt-4.1-mini",
    })
    # CLI tool to use per agent — defaults to "codex"; also supports "aider", "claude"
    cli_routing: dict[str, str] = Field(default_factory=lambda: {
        "PROMPT_GENERATOR": "codex",
        "CODE_GENERATOR": "codex",
        "CODE_REVIEWER": "codex",
    })


class GitConfig(BaseModel):
    enabled: bool = True
    remote: str = "origin"
    main_branch: str = "main"
    dev_branch: str = "dev"
    auto_create_feature_branches: bool = True


class ValidationConfig(BaseModel):
    lint: bool = True
    type_check: bool = True
    tests: bool = True
    security_scan: bool = True


class StorageConfig(BaseModel):
    db_path: str = "data/agent_os.db"

    @property
    def data_dir(self) -> "Path":
        """Return the absolute path to the data directory (parent of db_path)."""
        from pathlib import Path
        return Path(self.db_path).resolve().parent


class RequirementsConfig(BaseModel):
    path: str = "requirements.yaml"
    source: str = "device"          # "device" | "jira" | "asana" | "ado"
    # JIRA connection
    jira_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""
    # Asana connection
    asana_token: str = ""
    asana_project_id: str = ""
    # Azure DevOps connection
    ado_org: str = ""
    ado_token: str = ""
    ado_project: str = ""

    @field_validator(
        "path", "source", "jira_url", "jira_email", "jira_api_token",
        "jira_project_key", "asana_token", "asana_project_id",
        "ado_org", "ado_token", "ado_project",
        mode="before",
    )
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        """Convert YAML null (None) to empty string so str validation passes."""
        return "" if v is None else v


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1024, le=65535)


class BudgetConfig(BaseModel):
    token_budget_per_module: int = Field(default=20_000, ge=0)
    alert_threshold_pct: int = Field(default=80, ge=0, le=100)
    pause_at_limit: bool = True
    cost_per_1k_tokens: float = Field(default=0.01, ge=0)


class DependencyConfig(BaseModel):
    auto_create_venv: bool = True
    auto_install: bool = True
    venv_name: str = ".venv"


class ErrorHandlingConfig(BaseModel):
    max_json_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_base: float = Field(default=1.0, ge=0.1, le=30.0)
    retry_backoff_max: float = Field(default=30.0, ge=1.0, le=300.0)
    rollback_on_failure: bool = True
    skip_failed_validators: bool = True


class GitHubConfig(BaseModel):
    owner: str = ""
    repo: str = ""
    auto_push: bool = False
    auto_create_pr: bool = False


class SecretsConfig(BaseModel):
    openai_api_key: str = ""
    github_token: str = ""

    @field_validator("openai_api_key", "github_token", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return "" if v is None else v


class GitHubInputConfig(BaseModel):
    """Phase 5 — existing GitHub repo as read-only context for Module Maker."""

    enabled: bool = False
    source_repo_url: str = ""
    clone_depth: int = Field(default=1, ge=1, le=100)
    include_file_patterns: list[str] = Field(
        default_factory=lambda: ["**/*.py", "**/*.ts", "README.md"]
    )
    exclude_patterns: list[str] = Field(
        default_factory=lambda: [
            "**/node_modules/**",
            "**/.git/**",
            "**/__pycache__/**",
        ]
    )
    max_context_files: int = Field(default=50, ge=1, le=500)
    new_repo_suffix: str = "-agent-os-fork"


class GitHubReviewConfig(BaseModel):
    """Phase 2 — GitHub repo + requirements review mode (Mode C)."""

    source_repo_url: str = ""           # e.g. "https://github.com/owner/repo"
    requirements_path: str = ""         # local path to requirements.yaml for this review
    fork_repo_name: str = ""            # override fork name (default: <repo>-agent-os)
    branch_name: str = "agent-os-fixes" # branch for changes


class VCSConfig(BaseModel):
    """Version-control target — independent of requirements source."""
    provider: str = "github"  # "github" | "ado"


# ── Prompt Generator LLM provider ────────────────────────────────────────────

OLLAMA_MODELS: list[str] = [
    "llama3.1:8b",
    "llama3.2:3b",
    "llama3:latest",
    "qwen2.5:7b",
    "qwen2.5-coder:32b",
    "gemma3:4b",
    "mistral-nemo:latest",
    "ibm/granite-docling:latest",
    "nomic-embed-text:latest",
]


class OllamaConfig(BaseModel):
    """Connection settings for a remote (or local) Ollama service."""
    base_url: str = "http://localhost:11434"   # override with VPN remote GPU URL
    model: str = "llama3.1:8b"                # default model on the remote GPU
    timeout_seconds: int = Field(default=300, ge=30)


class PromptGeneratorConfig(BaseModel):
    """Which LLM backend to use for prompt generation."""
    provider: str = "ollama"          # "ollama" | "openai"
    ollama_model: str = "llama3.1:8b"
    openai_model: str = "gpt-4.1-mini"

    @field_validator("provider", mode="before")
    @classmethod
    def _valid_provider(cls, v: object) -> object:
        if v not in ("ollama", "openai"):
            return "ollama"
        return v


class CodeReviewerConfig(BaseModel):
    """Which LLM backend to use for code review."""
    provider: str = "openai"          # "openai" | "copilot" | "ollama"
    model: str = "gpt-4.1-mini"       # used for openai and copilot providers
    ollama_model: str = "llama3.1:8b" # used when provider == "ollama"

    @field_validator("provider", mode="before")
    @classmethod
    def _valid_provider(cls, v: object) -> object:
        if v not in ("openai", "copilot", "ollama"):
            return "openai"
        return v


class AIToolCredential(BaseModel):
    """Auth config for a single AI coding tool CLI."""
    enabled: bool = False
    auth_method: str = ""   # "api_key" | "account" | "oauth" | "local" | "bedrock" | "vertex"
    api_key: str = ""
    email: str = ""
    password: str = ""      # NOTE: stored hashed in transit; not persisted in plain text
    account_id: str = ""    # org/workspace ID where applicable
    endpoint: str = ""      # custom base URL / region override
    extra: dict = {}        # catch-all for tool-specific fields (e.g. ADC project, workspace)

    @field_validator(
        "auth_method", "api_key", "email", "password", "account_id", "endpoint",
        mode="before",
    )
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return "" if v is None else v


class AIToolsConfig(BaseModel):
    """Per-tool authentication & connection settings."""
    codex: AIToolCredential = AIToolCredential()       # OpenAI Codex CLI
    claude: AIToolCredential = AIToolCredential()      # Claude Code CLI
    gemini: AIToolCredential = AIToolCredential()      # Gemini CLI
    qwen: AIToolCredential = AIToolCredential()        # Qwen Coder CLI
    deepseek: AIToolCredential = AIToolCredential()    # DeepSeek CLI
    cursor: AIToolCredential = AIToolCredential()      # Cursor CLI (via cursor-headless)
    copilot: AIToolCredential = AIToolCredential()     # GitHub Copilot CLI


class AgentOSConfig(BaseModel):
    project: ProjectConfig = ProjectConfig()
    orchestrator: OrchestratorConfig = OrchestratorConfig()
    codex: CodexConfig = CodexConfig()
    prompt_framework: PromptFramework = PromptFramework.RCTCF
    git: GitConfig = GitConfig()
    validation: ValidationConfig = ValidationConfig()
    storage: StorageConfig = StorageConfig()
    requirements: RequirementsConfig = RequirementsConfig()
    api: ApiConfig = ApiConfig()
    budget: BudgetConfig = BudgetConfig()
    dependencies: DependencyConfig = DependencyConfig()
    error_handling: ErrorHandlingConfig = ErrorHandlingConfig()
    github: GitHubConfig = GitHubConfig()
    secrets: SecretsConfig = SecretsConfig()
    github_input: GitHubInputConfig = GitHubInputConfig()
    github_review: GitHubReviewConfig = GitHubReviewConfig()
    pipeline_mode: str = "standard"  # "standard" | "github_review"
    ai_tools: AIToolsConfig = AIToolsConfig()
    vcs: VCSConfig = VCSConfig()
    ollama: OllamaConfig = OllamaConfig()
    prompt_generator: PromptGeneratorConfig = PromptGeneratorConfig()
    code_reviewer: CodeReviewerConfig = CodeReviewerConfig()

    @field_validator("project")
    @classmethod
    def validate_project(cls, v: ProjectConfig) -> ProjectConfig:
        if v.root_path:
            path = Path(v.root_path).expanduser().resolve()
            v.root_path = str(path)
        return v

    @field_validator("storage")
    @classmethod
    def validate_storage(cls, v: StorageConfig) -> StorageConfig:
        db_dir = Path(v.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        return v
