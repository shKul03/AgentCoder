"""Settings routes — read/write config, test GitHub connection."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Depends

from ...config.env import mask_secret, resolve_secret
from ..deps import get_orchestrator, orch_holder
from ..schemas import (
    CliRoutingSettingsResponse,
    CodeReviewerSettingsResponse,
    GitHubSettingsResponse,
    GitHubReviewSettingsResponse,
    OllamaSettingsResponse,
    PipelineSettingsResponse,
    ProjectSettingsResponse,
    PromptGeneratorSettingsResponse,
    RequirementsSettingsResponse,
    SecretsSettingsResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    TestGitHubRequest,
    TestGitHubResponse,
    AIToolsSettingsResponse,
    AIToolCredentialResponse,
    VCSSettingsResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
def get_settings(orch=Depends(get_orchestrator)):
    """Return current settings with secrets masked."""
    cfg = orch.config
    project_root = cfg.project.root_path or "."

    # Supplement config-level secrets with DB-stored secrets as a fallback.
    # DB takes priority over .env / os.environ when the config value is empty.
    try:
        from ...storage.agent_config_repo import AgentConfigRepo
        _db_secrets = AgentConfigRepo(orch.db.conn).get_secrets()
    except Exception:
        _db_secrets = {}

    openai_resolved = resolve_secret(
        "openai_api_key",
        cfg.secrets.openai_api_key or _db_secrets.get("openai_api_key", ""),
        project_root,
    )
    github_resolved = resolve_secret(
        "github_token",
        cfg.secrets.github_token or _db_secrets.get("github_token", ""),
        project_root,
    )

    return SettingsResponse(
        secrets=SecretsSettingsResponse(
            openai_api_key=mask_secret(openai_resolved),
            github_token=mask_secret(github_resolved),
        ),
        github=GitHubSettingsResponse(
            owner=cfg.github.owner,
            repo=cfg.github.repo,
            auto_push=cfg.github.auto_push,
            auto_create_pr=cfg.github.auto_create_pr,
        ),
        project=ProjectSettingsResponse(
            name=cfg.project.name,
            root_path=cfg.project.root_path,
            language=cfg.project.language,
            repo_name=getattr(cfg.project, 'repo_name', ''),
            feature_branch=getattr(cfg.project, 'feature_branch', 'dev'),
            prompt_file_path=getattr(cfg.project, 'prompt_file_path', ''),
        ),
        pipeline=PipelineSettingsResponse(
            max_iterations=cfg.orchestrator.max_iterations,
            convergence_rule=cfg.orchestrator.convergence_rule.value,
            auto_approve_hitl=cfg.orchestrator.auto_approve_hitl,
        ),
        cli_routing=CliRoutingSettingsResponse(
            PROMPT_GENERATOR=cfg.codex.cli_routing.get("PROMPT_GENERATOR", "codex"),
            CODE_GENERATOR=cfg.codex.cli_routing.get("CODE_GENERATOR", "codex"),
            CODE_REVIEWER=cfg.codex.cli_routing.get("CODE_REVIEWER", "codex"),
        ),
        requirements=RequirementsSettingsResponse(
            path=cfg.requirements.path,
            source=getattr(cfg.requirements, 'source', 'device'),
            jira_url=getattr(cfg.requirements, 'jira_url', ''),
            jira_email=getattr(cfg.requirements, 'jira_email', ''),
            jira_api_token=getattr(cfg.requirements, 'jira_api_token', ''),
            jira_project_key=getattr(cfg.requirements, 'jira_project_key', ''),
            asana_token=getattr(cfg.requirements, 'asana_token', ''),
            asana_project_id=getattr(cfg.requirements, 'asana_project_id', ''),
            ado_org=getattr(cfg.requirements, 'ado_org', ''),
            ado_token=getattr(cfg.requirements, 'ado_token', ''),
            ado_project=getattr(cfg.requirements, 'ado_project', ''),
        ),
        github_review=GitHubReviewSettingsResponse(
            source_repo_url=cfg.github_review.source_repo_url,
            requirements_path=cfg.github_review.requirements_path,
            fork_repo_name=cfg.github_review.fork_repo_name,
            branch_name=cfg.github_review.branch_name,
        ),
        pipeline_mode=getattr(cfg, 'pipeline_mode', 'standard'),
        ai_tools=_serialize_ai_tools(cfg),
        vcs=VCSSettingsResponse(
            provider=getattr(getattr(cfg, 'vcs', None), 'provider', 'github') or 'github',
        ),
        ollama=OllamaSettingsResponse(
            base_url=getattr(getattr(cfg, 'ollama', None), 'base_url', 'http://localhost:11434') or 'http://localhost:11434',
            model=getattr(getattr(cfg, 'ollama', None), 'model', 'llama3.1:8b') or 'llama3.1:8b',
            timeout_seconds=getattr(getattr(cfg, 'ollama', None), 'timeout_seconds', 300) or 300,
        ),
        prompt_generator=PromptGeneratorSettingsResponse(
            provider=getattr(getattr(cfg, 'prompt_generator', None), 'provider', 'ollama') or 'ollama',
            ollama_model=getattr(getattr(cfg, 'prompt_generator', None), 'ollama_model', 'llama3.1:8b') or 'llama3.1:8b',
            openai_model=getattr(getattr(cfg, 'prompt_generator', None), 'openai_model', 'gpt-4.1-mini') or 'gpt-4.1-mini',
        ),
        code_reviewer=CodeReviewerSettingsResponse(
            provider=getattr(getattr(cfg, 'code_reviewer', None), 'provider', 'openai') or 'openai',
            model=getattr(getattr(cfg, 'code_reviewer', None), 'model', 'gpt-4.1-mini') or 'gpt-4.1-mini',
            ollama_model=getattr(getattr(cfg, 'code_reviewer', None), 'ollama_model', 'llama3.1:8b') or 'llama3.1:8b',
        ),
    )


def _mask(val: str) -> str:
    return "***" if val else ""


def _cred_to_response(cred: object) -> AIToolCredentialResponse:
    return AIToolCredentialResponse(
        enabled=getattr(cred, 'enabled', False),
        auth_method=getattr(cred, 'auth_method', ''),
        api_key=_mask(getattr(cred, 'api_key', '')),
        email=getattr(cred, 'email', ''),
        account_id=getattr(cred, 'account_id', ''),
        endpoint=getattr(cred, 'endpoint', ''),
        extra=getattr(cred, 'extra', {}),
    )


def _serialize_ai_tools(cfg: object) -> AIToolsSettingsResponse:
    at = getattr(cfg, 'ai_tools', None)
    if at is None:
        return AIToolsSettingsResponse()
    return AIToolsSettingsResponse(
        codex=_cred_to_response(getattr(at, 'codex', object())),
        claude=_cred_to_response(getattr(at, 'claude', object())),
        gemini=_cred_to_response(getattr(at, 'gemini', object())),
        qwen=_cred_to_response(getattr(at, 'qwen', object())),
        deepseek=_cred_to_response(getattr(at, 'deepseek', object())),
        cursor=_cred_to_response(getattr(at, 'cursor', object())),
        copilot=_cred_to_response(getattr(at, 'copilot', object())),
    )


@router.put("", response_model=SettingsResponse)
def update_settings(body: SettingsUpdateRequest, orch=Depends(get_orchestrator)):
    """Update settings, write to config.yaml, and hot-reload into the orchestrator."""
    cfg = orch.config

    # Apply secrets (only overwrite non-empty, non-masked values).
    # mask_secret() produces "XYZ...ABCD" (3 + "..." + 4 = 10 chars); treat
    # that pattern the same as the legacy "***" sentinel — i.e. skip it so
    # saving the pre-filled display value never overwrites the real token.
    def _is_masked(val: str) -> bool:
        return (
            val.startswith("***")
            or (len(val) == 10 and val[3:6] == "...")
        )

    _new_openai = ""
    _new_github = ""
    if body.secrets is not None:
        if body.secrets.openai_api_key and not _is_masked(body.secrets.openai_api_key):
            cfg.secrets.openai_api_key = body.secrets.openai_api_key
            _new_openai = body.secrets.openai_api_key
        if body.secrets.github_token and not _is_masked(body.secrets.github_token):
            cfg.secrets.github_token = body.secrets.github_token
            _new_github = body.secrets.github_token

    if body.github is not None:
        cfg.github.owner = body.github.owner
        cfg.github.repo = body.github.repo
        cfg.github.auto_push = body.github.auto_push
        cfg.github.auto_create_pr = body.github.auto_create_pr

    if body.project is not None:
        cfg.project.name = body.project.name
        cfg.project.root_path = body.project.root_path
        cfg.project.language = body.project.language
        cfg.project.repo_name = getattr(body.project, 'repo_name', '')
        cfg.project.feature_branch = getattr(body.project, 'feature_branch', 'dev')
        cfg.project.prompt_file_path = getattr(body.project, 'prompt_file_path', '')

    if body.pipeline is not None:
        cfg.orchestrator.max_iterations = body.pipeline.max_iterations
        cfg.orchestrator.auto_approve_hitl = body.pipeline.auto_approve_hitl
        from ...config.schema import ConvergenceRule
        cfg.orchestrator.convergence_rule = ConvergenceRule(body.pipeline.convergence_rule)

    if body.cli_routing is not None:
        cfg.codex.cli_routing = {
            "PROMPT_GENERATOR": body.cli_routing.PROMPT_GENERATOR,
            "CODE_GENERATOR": body.cli_routing.CODE_GENERATOR,
            "CODE_REVIEWER": body.cli_routing.CODE_REVIEWER,
        }

    if body.requirements is not None:
        if body.requirements.path:
            cfg.requirements.path = body.requirements.path
        cfg.requirements.source = body.requirements.source
        cfg.requirements.jira_url = body.requirements.jira_url
        cfg.requirements.jira_email = body.requirements.jira_email
        if body.requirements.jira_api_token and not body.requirements.jira_api_token.startswith('***'):
            cfg.requirements.jira_api_token = body.requirements.jira_api_token
        cfg.requirements.jira_project_key = body.requirements.jira_project_key
        if body.requirements.asana_token and not body.requirements.asana_token.startswith('***'):
            cfg.requirements.asana_token = body.requirements.asana_token
        cfg.requirements.asana_project_id = body.requirements.asana_project_id
        cfg.requirements.ado_org = body.requirements.ado_org
        if body.requirements.ado_token and not body.requirements.ado_token.startswith('***'):
            cfg.requirements.ado_token = body.requirements.ado_token
        cfg.requirements.ado_project = body.requirements.ado_project

    if body.pipeline_mode is not None:
        cfg.pipeline_mode = body.pipeline_mode

    if body.github_review is not None:
        cfg.github_review.source_repo_url = body.github_review.source_repo_url
        cfg.github_review.requirements_path = body.github_review.requirements_path
        cfg.github_review.fork_repo_name = body.github_review.fork_repo_name
        cfg.github_review.branch_name = body.github_review.branch_name or 'agent-os-fixes'

    if body.ai_tools is not None:
        at = getattr(cfg, 'ai_tools', None)
        if at is None:
            from ...config.schema import AIToolsConfig
            cfg.ai_tools = AIToolsConfig()
            at = cfg.ai_tools
        for tool_name in ('codex', 'claude', 'gemini', 'qwen', 'deepseek', 'cursor', 'copilot'):
            incoming = getattr(body.ai_tools, tool_name, None)
            if incoming is None:
                continue
            cred = getattr(at, tool_name)
            cred.enabled = incoming.enabled
            cred.auth_method = incoming.auth_method
            cred.email = incoming.email
            cred.account_id = incoming.account_id
            cred.endpoint = incoming.endpoint
            cred.extra = incoming.extra
            # Only overwrite API key if the caller sent a real value (not masked)
            if incoming.api_key and not incoming.api_key.startswith('***'):
                cred.api_key = incoming.api_key

    if body.vcs is not None:
        vcs_cfg = getattr(cfg, 'vcs', None)
        if vcs_cfg is None:
            from ...config.schema import VCSConfig
            cfg.vcs = VCSConfig()
            vcs_cfg = cfg.vcs
        vcs_cfg.provider = body.vcs.provider if body.vcs.provider in ('github', 'ado') else 'github'

    if body.ollama is not None:
        ollama_cfg = getattr(cfg, 'ollama', None)
        if ollama_cfg is None:
            from ...config.schema import OllamaConfig
            cfg.ollama = OllamaConfig()
            ollama_cfg = cfg.ollama
        if body.ollama.base_url:
            ollama_cfg.base_url = body.ollama.base_url
        if body.ollama.model:
            ollama_cfg.model = body.ollama.model
        ollama_cfg.timeout_seconds = body.ollama.timeout_seconds

    if body.prompt_generator is not None:
        pg_cfg = getattr(cfg, 'prompt_generator', None)
        if pg_cfg is None:
            from ...config.schema import PromptGeneratorConfig
            cfg.prompt_generator = PromptGeneratorConfig()
            pg_cfg = cfg.prompt_generator
        if body.prompt_generator.provider in ('ollama', 'openai'):
            pg_cfg.provider = body.prompt_generator.provider
        if body.prompt_generator.ollama_model:
            pg_cfg.ollama_model = body.prompt_generator.ollama_model
        if body.prompt_generator.openai_model:
            pg_cfg.openai_model = body.prompt_generator.openai_model

    if body.code_reviewer is not None:
        cr_cfg = getattr(cfg, 'code_reviewer', None)
        if cr_cfg is None:
            from ...config.schema import CodeReviewerConfig
            cfg.code_reviewer = CodeReviewerConfig()
            cr_cfg = cfg.code_reviewer
        if body.code_reviewer.provider in ('openai', 'copilot', 'ollama'):
            cr_cfg.provider = body.code_reviewer.provider
        if body.code_reviewer.model:
            cr_cfg.model = body.code_reviewer.model
        if body.code_reviewer.ollama_model:
            cr_cfg.ollama_model = body.code_reviewer.ollama_model

    # Persist to config.yaml (no-op when config_path is None, e.g. in tests)
    _write_config_yaml(cfg, orch_holder.config_path)

    # Mirror model_routing and secrets to DB so they survive process restarts.
    try:
        from ...storage.agent_config_repo import AgentConfigRepo
        _repo = AgentConfigRepo(orch.db.conn)
        if cfg.codex.model_routing:
            _repo.set_model_routing(dict(cfg.codex.model_routing))
        if _new_openai or _new_github:
            _repo.set_secrets(openai_api_key=_new_openai, github_token=_new_github)
            # Inject into this process's os.environ immediately — resolve_secret
            # will find it without needing a DB round-trip
            import os as _os
            if _new_openai:
                _os.environ["OPENAI_API_KEY"] = _new_openai
            if _new_github:
                _os.environ["GITHUB_TOKEN"] = _new_github
            # Also persist to .env at Agent OS root so the token survives
            # a uvicorn --reload restart (which kills the process).
            # Use the config file's directory, not CWD, so the path is always
            # correct regardless of where uvicorn was launched from.
            try:
                from pathlib import Path as _Path
                _agent_os_root = (
                    _Path(orch_holder.config_path).parent
                    if orch_holder.config_path
                    else _Path(".").resolve()
                )
                _env_path = _agent_os_root / ".env"
                _lines: list[str] = []
                if _env_path.exists():
                    _lines = _env_path.read_text().splitlines()
                # Upsert each key
                def _upsert(lines: list[str], key: str, val: str) -> list[str]:
                    prefix = f"{key}="
                    updated = False
                    result = []
                    for ln in lines:
                        if ln.startswith(prefix):
                            result.append(f'{key}="{val}"')
                            updated = True
                        else:
                            result.append(ln)
                    if not updated:
                        result.append(f'{key}="{val}"')
                    return result
                if _new_openai:
                    _lines = _upsert(_lines, "OPENAI_API_KEY", _new_openai)
                if _new_github:
                    _lines = _upsert(_lines, "GITHUB_TOKEN", _new_github)
                _env_path.write_text("\n".join(_lines) + "\n")
                logger.info("Saved token(s) to %s", _env_path)
            except Exception as _env_err:
                logger.debug("Could not write .env file: %s", _env_err)
    except Exception as _e:
        logger.warning("Could not mirror settings to DB: %s", _e)

    logger.info("Settings updated and persisted to config.yaml")

    # Return the updated (masked) settings
    return get_settings(orch)


@router.post("/test-github", response_model=TestGitHubResponse)
def test_github_connection(body: TestGitHubRequest = TestGitHubRequest(), orch=Depends(get_orchestrator)):
    """Test a GitHub token by calling the /user endpoint.

    If ``body.token`` is provided (unsaved value from the UI), it is used
    directly.  Otherwise the saved token from config / env is resolved.
    """
    cfg = orch.config
    project_root = cfg.project.root_path or "."

    # Prefer the token sent in the request (allows testing before saving).
    if body.token and not body.token.startswith("***") and "..." not in body.token:
        token = body.token
    else:
        try:
            from ...storage.agent_config_repo import AgentConfigRepo
            _db_tok = AgentConfigRepo(orch.db.conn).get_secrets().get("github_token", "")
        except Exception:
            _db_tok = ""
        token = resolve_secret("github_token", cfg.secrets.github_token or _db_tok, project_root)

    if not token:
        return TestGitHubResponse(valid=False, message="No GitHub token configured")

    try:
        resp = httpx.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return TestGitHubResponse(
                valid=True,
                user=data.get("login", ""),
                message=f"Authenticated as {data.get('login', 'unknown')}",
            )
        return TestGitHubResponse(
            valid=False,
            message=f"GitHub API returned {resp.status_code}",
        )
    except httpx.HTTPError as exc:
        return TestGitHubResponse(valid=False, message=f"Connection error: {str(exc)[:200]}")


def _write_config_yaml(cfg, config_path: Path | None = None) -> None:
    """Serialize user-editable settings back to the config YAML file.

    If ``config_path`` is None (e.g. in-memory / test configurations) the
    function is a no-op — it will never write to the project's config.yaml.

    Keys that are not exposed via the Settings UI (``storage.db_path``,
    ``codex.model_routing``, ``budget.*``) are read from the existing file and
    preserved verbatim, so a test or a mis-configured in-memory object can
    never clobber values the user set manually.

    Secrets are always written as empty strings; real values should live in
    ``.env`` or environment variables, not in the YAML file.
    """
    if config_path is None:
        return  # no persistent file — skip (in-memory / test orchestrator)

    # Read the existing file to preserve keys not managed by the Settings UI.
    existing: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            existing = yaml.safe_load(f) or {}

    # Preserve storage, model_routing, cli_routing and budget from the file on disk.
    existing_storage = existing.get("storage", {})
    existing_model_routing = existing.get("codex", {}).get("model_routing", {})
    existing_cli_routing = existing.get("codex", {}).get("cli_routing", {})
    existing_budget = existing.get("budget", {})

    data = {
        "project": {
            "name": cfg.project.name,
            "root_path": cfg.project.root_path,
            "language": cfg.project.language,
            "repo_name": getattr(cfg.project, 'repo_name', ''),
            "feature_branch": getattr(cfg.project, 'feature_branch', 'dev'),
            "prompt_file_path": getattr(cfg.project, 'prompt_file_path', ''),
        },
        "orchestrator": {
            "max_iterations": cfg.orchestrator.max_iterations,
            "auto_approve_hitl": cfg.orchestrator.auto_approve_hitl,
            "hitl_timeout_seconds": cfg.orchestrator.hitl_timeout_seconds,
            "convergence_rule": cfg.orchestrator.convergence_rule.value,
        },
        "codex": {
            "model": cfg.codex.model,
            "timeout_seconds": cfg.codex.timeout_seconds,
            "max_retries": cfg.codex.max_retries,
            # Preserve model_routing exactly as the user wrote it in the file.
            **({
                "model_routing": existing_model_routing
            } if existing_model_routing else {}),
            # Persist cli_routing — use live config value, fall back to existing file value
            "cli_routing": cfg.codex.cli_routing or existing_cli_routing or {},
        },
        "prompt_framework": cfg.prompt_framework.value,
        "git": {
            "enabled": cfg.git.enabled,
            "remote": cfg.git.remote,
            "main_branch": cfg.git.main_branch,
            "dev_branch": cfg.git.dev_branch,
            "auto_create_feature_branches": cfg.git.auto_create_feature_branches,
        },
        "validation": {
            "lint": cfg.validation.lint,
            "type_check": cfg.validation.type_check,
            "tests": cfg.validation.tests,
            "security_scan": cfg.validation.security_scan,
        },
        "requirements": {
            "path": cfg.requirements.path,
            "ado_org": getattr(cfg.requirements, "ado_org", ""),
            # Only write non-empty token (never write a masked placeholder)
            **({"ado_token": cfg.requirements.ado_token}
               if getattr(cfg.requirements, "ado_token", "")
               and not cfg.requirements.ado_token.startswith("***")
               else {}),
            "ado_project": getattr(cfg.requirements, "ado_project", ""),
        },
        # Preserve db_path from the file — it is a deployment setting, not a
        # user-editable field exposed through the Settings UI.
        "storage": {
            "db_path": existing_storage.get("db_path", "data/agent_os.db"),
        },
        "api": {"host": cfg.api.host, "port": cfg.api.port},
        # Preserve budget from the file so the user's manual edits survive.
        "budget": {
            "token_budget_per_module": existing_budget.get(
                "token_budget_per_module", cfg.budget.token_budget_per_module
            ),
            "alert_threshold_pct": existing_budget.get(
                "alert_threshold_pct", cfg.budget.alert_threshold_pct
            ),
            "pause_at_limit": existing_budget.get(
                "pause_at_limit", cfg.budget.pause_at_limit
            ),
            "cost_per_1k_tokens": existing_budget.get(
                "cost_per_1k_tokens", cfg.budget.cost_per_1k_tokens
            ),
        },
        "dependencies": {
            "auto_create_venv": cfg.dependencies.auto_create_venv,
            "auto_install": cfg.dependencies.auto_install,
            "venv_name": cfg.dependencies.venv_name,
        },
        "error_handling": {
            "max_json_retries": cfg.error_handling.max_json_retries,
            "retry_backoff_base": cfg.error_handling.retry_backoff_base,
            "retry_backoff_max": cfg.error_handling.retry_backoff_max,
            "rollback_on_failure": cfg.error_handling.rollback_on_failure,
            "skip_failed_validators": cfg.error_handling.skip_failed_validators,
        },
        "github": {
            "owner": cfg.github.owner,
            "repo": cfg.github.repo,
            "auto_push": cfg.github.auto_push,
            "auto_create_pr": cfg.github.auto_create_pr,
        },
        "secrets": {
            "openai_api_key": "",
            "github_token": "",
        },
        "pipeline_mode": getattr(cfg, 'pipeline_mode', 'standard'),
        "github_review": {
            "source_repo_url": cfg.github_review.source_repo_url,
            "requirements_path": cfg.github_review.requirements_path,
            "fork_repo_name": cfg.github_review.fork_repo_name,
            "branch_name": cfg.github_review.branch_name,
        },
        "ollama": {
            "base_url": getattr(getattr(cfg, 'ollama', None), 'base_url', 'http://localhost:11434'),
            "model": getattr(getattr(cfg, 'ollama', None), 'model', 'llama3.1:8b'),
            "timeout_seconds": getattr(getattr(cfg, 'ollama', None), 'timeout_seconds', 300),
        },
        "prompt_generator": {
            "provider": getattr(getattr(cfg, 'prompt_generator', None), 'provider', 'ollama'),
            "ollama_model": getattr(getattr(cfg, 'prompt_generator', None), 'ollama_model', 'llama3.1:8b'),
            "openai_model": getattr(getattr(cfg, 'prompt_generator', None), 'openai_model', 'gpt-4.1-mini'),
        },
        "code_reviewer": {
            "provider": getattr(getattr(cfg, 'code_reviewer', None), 'provider', 'openai'),
            "model": getattr(getattr(cfg, 'code_reviewer', None), 'model', 'gpt-4.1-mini'),
            "ollama_model": getattr(getattr(cfg, 'code_reviewer', None), 'ollama_model', 'llama3.1:8b'),
        },
    }

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
