"""Standard pipeline step functions (non-GitHub-review mode)."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from ...ado.client import AzureDevOpsClient
from ...storage.models import PipelineStatus
from ...utils.requirements_name import extract_project_name, name_to_slug
from ...vcs.factory import make_vcs_client
from .base import StepContext

logger = logging.getLogger(__name__)


def step_idle(ctx: StepContext) -> None:
    """IDLE → LOADING_REQUIREMENTS."""
    ctx.state_mgr.transition_to(PipelineStatus.LOADING_REQUIREMENTS)
    ctx.emit("state_changed")


def step_load_requirements(ctx: StepContext) -> None:
    """LOADING_REQUIREMENTS → PROMPT_GENERATION (or ANALYSING_DEPENDENCIES in GHR mode)."""
    from ...requirements.parser import RequirementsParser

    req_path = ctx.config.requirements.path
    logger.info("Loading requirements from: %s", req_path)
    ctx.emit("loading_requirements", {"path": req_path})

    parser = RequirementsParser(db=ctx.db)
    stats = parser.load_and_store(req_path)
    logger.info("Requirements loaded: %s", stats)

    title = extract_project_name(req_path, fallback="Agent OS Project")
    slug = name_to_slug(title)
    ctx.config.project.name = title
    ctx.config.project.repo_name = slug
    logger.info("Project name set from requirements: %s (repo=%s)", title, slug)

    state = ctx.state_mgr.state
    if ctx.config.pipeline_mode == "github_review":
        ctx.state_mgr.transition_to(
            PipelineStatus.ANALYSING_DEPENDENCIES,
            iteration=max(state.current_iteration, 1),
        )
    else:
        ctx.state_mgr.transition_to(
            PipelineStatus.PROMPT_GENERATION,
            iteration=max(state.current_iteration, 1),
        )
    ctx.emit("state_changed")


def step_prompt_generation(ctx: StepContext) -> None:
    """PROMPT_GENERATION → HITL_PROMPT_REVIEW."""
    from ...prompt_generator.runner import PromptGeneratorRunner

    state = ctx.state_mgr.state
    iteration = state.current_iteration
    logger.info("Prompt generation — iteration %d", iteration)
    ctx.emit("prompt_generation_started", {"iteration": iteration})

    ctx.upsert_iteration(iteration)

    review_json: Optional[dict] = None
    if iteration > 1:
        review_content = state.metadata.get("review_json_content")
        if review_content:
            try:
                review_json = json.loads(review_content)
            except Exception:
                review_json = {"raw": review_content}

    requirements_text: Optional[str] = None
    if iteration == 1:
        req_path = getattr(ctx.config.requirements, "path", "")
        if req_path:
            req_file = Path(req_path)
            if req_file.exists():
                requirements_text = req_file.read_text(encoding="utf-8")

    _pg_session = f"pg-{iteration}-{uuid.uuid4().hex[:8]}"
    _pg_cfg = getattr(ctx.config, "prompt_generator", None)
    _pg_provider = getattr(_pg_cfg, "provider", "ollama")
    if _pg_provider == "openai":
        model_pg = getattr(_pg_cfg, "openai_model", None) or "gpt-4.1-mini"
    else:
        model_pg = f"ollama/{getattr(_pg_cfg, 'ollama_model', None) or 'llama3.1:8b'}"
    ctx.emit_terminal("session_start", "PROMPT_GENERATOR", _pg_session,
                      iteration=iteration, module_id="", model=model_pg)

    def _on_stdout(line: str) -> None:
        ctx.emit("prompt_token", {"line": line})
        ctx.emit_terminal("token", "PROMPT_GENERATOR", _pg_session,
                          text=line, stream="stdout", iteration=iteration, module_id="")

    runner = PromptGeneratorRunner(ctx.config)
    try:
        prompt_text = runner.run(
            iteration=iteration,
            requirements_text=requirements_text,
            review_json=review_json,
            on_stdout=_on_stdout,
        )
    except Exception as exc:
        logger.exception("Prompt generator failed: %s", exc)
        ctx.emit_terminal("session_end", "PROMPT_GENERATOR", _pg_session, exit_code=1)
        err_msg = str(exc)
        ctx.state_mgr.transition_to(
            PipelineStatus.HITL_PROMPT_REVIEW,
            metadata={"prompt_gen_failed": True, "prompt_gen_error": err_msg},
        )
        ctx.emit("prompt_gen_failed", {"iteration": iteration, "error": err_msg})
        ctx.emit("hitl_gate", {"gate": PipelineStatus.HITL_PROMPT_REVIEW.value})
        return

    ctx.state_mgr.update_metadata({"prompt_content": prompt_text})
    logger.info("Prompt generation complete — %d chars", len(prompt_text))
    ctx.emit_terminal("session_end", "PROMPT_GENERATOR", _pg_session, exit_code=0)
    ctx.emit("prompt_generation_complete", {"iteration": iteration, "char_count": len(prompt_text)})

    ctx.state_mgr.transition_to(PipelineStatus.HITL_PROMPT_REVIEW)
    ctx.emit("hitl_gate", {"gate": PipelineStatus.HITL_PROMPT_REVIEW.value})


def _resolve_ado_ids(ctx: StepContext) -> tuple[str, str, list[int]]:
    """Return (ado_org, ado_token, work_item_ids) from state metadata / config."""
    meta = ctx.state_mgr.state.metadata
    ado_org = meta.get("ado_org", "") or getattr(ctx.config.requirements, "ado_org", "")
    ado_token = meta.get("ado_token", "") or getattr(ctx.config.requirements, "ado_token", "")
    story_id = ctx.state_mgr.state.current_story_id
    if story_id and str(story_id).isdigit():
        work_item_ids: list[int] = [int(story_id)]
    else:
        work_item_ids = meta.get("ado_work_item_ids", [])
    return ado_org, ado_token, work_item_ids


def step_code_generation(
    ctx: StepContext,
    provision_project_dir: object,  # callable() -> str
    active_codex_ref: list,         # mutable [wrapper_or_None]
    code_gen_stop_requested: object,  # threading.Event
) -> None:
    """CODE_GENERATION → CODE_REVIEW."""
    from ...code_generator.runner import CodeGeneratorRunner

    state = ctx.state_mgr.state
    iteration = state.current_iteration
    logger.info("Code generation — iteration %d", iteration)
    ctx.emit("code_generation_started", {"iteration": iteration})

    prompt_path = getattr(ctx.config.project, "prompt_file_path", "") or "data/prompts/latest.md"
    working_dir = getattr(ctx.config.project, "root_path", "") or ""
    if not working_dir:
        working_dir = provision_project_dir()  # type: ignore[operator]
    if not working_dir:
        working_dir = "."

    cli_tool = state.metadata.get("selected_cli_tool")
    if cli_tool:
        ctx.config.codex.cli_routing["CODE_GENERATOR"] = cli_tool
        logger.info("CLI tool overridden to '%s' for code generation step", cli_tool)
    cli_model = state.metadata.get("selected_cli_model")
    if cli_model:
        ctx.config.codex.model_routing["CODE_GENERATOR"] = cli_model

    pr_number: Optional[int] = None
    pr_raw = state.metadata.get("pr_number")
    if pr_raw is not None:
        try:
            pr_number = int(pr_raw)
        except (TypeError, ValueError):
            pass

    _cg_session = f"cg-{iteration}-{uuid.uuid4().hex[:8]}"
    _cg_model = ctx.config.codex.model_routing.get("CODE_GENERATOR") or ctx.config.codex.default_model or ""
    _cg_tool = cli_tool or ctx.config.codex.cli_routing.get("CODE_GENERATOR", "codex")
    ctx.emit_terminal("session_start", "CODE_GENERATOR", _cg_session,
                      iteration=iteration, module_id="", model=_cg_model, tool=_cg_tool)

    def _on_stdout(line: str) -> None:
        ctx.emit("codex_stdout", {"line": line})
        ctx.emit_terminal("line", "CODE_GENERATOR", _cg_session,
                          line=line, stream="stdout", iteration=iteration, module_id="")

    def _on_stderr(line: str) -> None:
        ctx.emit("codex_stderr", {"line": line})
        ctx.emit_terminal("line", "CODE_GENERATOR", _cg_session,
                          line=line, stream="stderr", iteration=iteration, module_id="")

    # Activate ADO items New → Active on first iteration
    if iteration == 1:
        ado_org, ado_token, wi_ids = _resolve_ado_ids(ctx)
        if ado_org and ado_token and wi_ids:
            AzureDevOpsClient(ado_org, ado_token).activate(wi_ids)
        else:
            logger.debug("[ADO] No credentials or IDs — skipping New→Active transition")

    runner = CodeGeneratorRunner(ctx.config, vcs_client=make_vcs_client(ctx.config))
    active_codex_ref[0] = runner._codex
    code_gen_stop_requested.clear()  # type: ignore[attr-defined]
    try:
        gen_result = runner.run(
            prompt_path=prompt_path,
            working_dir=working_dir,
            iteration=iteration,
            pr_number=pr_number,
            on_stdout=_on_stdout,
            on_stderr=_on_stderr,
        )
    except Exception as exc:
        active_codex_ref[0] = None
        logger.exception("Code generator raised: %s", exc)
        ctx.emit_terminal("session_end", "CODE_GENERATOR", _cg_session, exit_code=1)
        if code_gen_stop_requested.is_set():  # type: ignore[attr-defined]
            ctx.state_mgr.transition_to(
                PipelineStatus.CODE_GEN_STOPPED,
                metadata={"stopped_working_dir": str(working_dir)},
            )
            ctx.emit("code_gen_stopped", {"iteration": iteration})
        else:
            err_msg = str(exc)
            ctx.state_mgr.transition_to(
                PipelineStatus.CODE_GEN_FAILED,
                metadata={"code_gen_failed": True, "code_gen_error": err_msg},
            )
            ctx.emit("code_gen_failed", {"iteration": iteration, "error": err_msg})
        return
    active_codex_ref[0] = None

    if code_gen_stop_requested.is_set():  # type: ignore[attr-defined]
        ctx.emit_terminal("session_end", "CODE_GENERATOR", _cg_session, exit_code=-2)
        ctx.state_mgr.transition_to(
            PipelineStatus.CODE_GEN_STOPPED,
            metadata={"stopped_working_dir": str(working_dir)},
        )
        ctx.emit("code_gen_stopped", {"iteration": iteration, "working_dir": str(working_dir)})
        return

    metadata_update: dict = {"completion_status": gen_result.completion.status.value}
    if cli_tool:
        metadata_update["cli_tool_used"] = cli_tool
    if gen_result.pr_number is not None:
        metadata_update["pr_number"] = gen_result.pr_number
        metadata_update["pr_url"] = gen_result.pr_url
    if gen_result.git_errors:
        metadata_update["git_errors"] = gen_result.git_errors
    ctx.state_mgr.update_metadata(metadata_update)

    if cli_tool:
        try:
            ctx.db.conn.execute(
                "UPDATE iterations SET cli_tool_used = ? WHERE iteration_number = ?",
                (cli_tool, iteration),
            )
            ctx.db.conn.commit()
        except Exception:
            logger.debug("Could not update iterations.cli_tool_used", exc_info=True)

    ctx.upsert_iteration(
        iteration,
        prompt_content=ctx.state_mgr.state.metadata.get("prompt_content", ""),
        cli_tool_used=cli_tool or ctx.config.codex.cli_routing.get("CODE_GENERATOR", ""),
    )

    if gen_result.git_errors:
        for git_err in gen_result.git_errors:
            ctx.emit_terminal("line", "CODE_GENERATOR", _cg_session,
                              line=f"[git] {git_err}", stream="stderr",
                              iteration=iteration, module_id="")

    _is_incomplete = gen_result.completion.status.value in ("failed", "partial")
    ctx.emit_terminal("session_end", "CODE_GENERATOR", _cg_session,
                      exit_code=0 if not _is_incomplete else 1)

    if _is_incomplete:
        err_msg = gen_result.completion.reason or "Code generation incomplete"
        logger.warning("Code generation incomplete — transitioning to CODE_GEN_FAILED: %s", err_msg)
        ctx.state_mgr.transition_to(
            PipelineStatus.CODE_GEN_FAILED,
            metadata={"code_gen_failed": True, "code_gen_error": err_msg},
        )
        ctx.emit("code_gen_failed", {"iteration": iteration, "error": err_msg})
        return

    ctx.emit("code_generation_complete", {
        "iteration": iteration,
        "pr_number": gen_result.pr_number,
        "pr_url": gen_result.pr_url,
        "retried": gen_result.retried,
    })
    ctx.state_mgr.transition_to(PipelineStatus.CODE_REVIEW)
    ctx.emit("state_changed")


def step_code_review(ctx: StepContext) -> None:
    """CODE_REVIEW → HITL_REVIEW_DECISION (or PIPELINE_COMPLETE if accepted + merged)."""
    from ...code_reviewer.runner import CodeReviewerRunner

    state = ctx.state_mgr.state
    iteration = state.current_iteration
    logger.info("Code review — iteration %d", iteration)

    ctx.state_mgr.update_metadata({
        "code_review_failed": False,
        "code_review_error": "",
        "pr_failed": False,
        "pr_error": "",
    })
    ctx.emit("code_review_started", {"iteration": iteration})

    pr_number: Optional[int] = None
    pr_raw = state.metadata.get("pr_number")
    if pr_raw is not None:
        try:
            pr_number = int(pr_raw)
        except (TypeError, ValueError):
            pass

    feature_branch = ctx.config.project.feature_branch or "dev"

    # Fallback 1: discover open PR
    if pr_number is None:
        logger.info("PR number not in metadata — attempting discovery via VCS API")
        try:
            vcs = ctx.make_project_vcs()
            if vcs is not None:
                discovered = vcs.find_open_pr(feature_branch)
                if discovered is not None:
                    pr_number = discovered
                    ctx.state_mgr.update_metadata({"pr_number": pr_number})
                    logger.info("Discovered PR #%d for branch '%s'", pr_number, feature_branch)
        except Exception:
            logger.debug("PR discovery failed", exc_info=True)

    # Fallback 2: create a new PR
    if pr_number is None:
        logger.info("PR discovery found nothing — attempting to create PR for review")
        try:
            vcs = ctx.make_project_vcs()
            if vcs is not None:
                _actor = getattr(vcs, "_owner", "unknown")
                _actor_repo = getattr(vcs, "_repo", "")
                pr_title = f"[Agent OS] Iteration {iteration} — implementation"
                pr_body = (
                    f"Automated pull request opened by Agent OS (code review fallback).\n\n"
                    f"**Iteration:** {iteration}\n"
                )
                pr_result = vcs.create_pr(title=pr_title, head=feature_branch, base="main", body=pr_body)
                if pr_result.success and pr_result.data:
                    pr_number = pr_result.data.get("number")
                    ctx.state_mgr.update_metadata({"pr_number": pr_number})
                    logger.info("[actor: %s] Created PR #%d in %s/%s", _actor, pr_number, _actor, _actor_repo)
                else:
                    logger.warning("[actor: %s] Fallback PR creation failed in %s/%s: %s",
                                   _actor, _actor, _actor_repo, pr_result.error)
                    ctx.state_mgr.update_metadata({"pr_error": pr_result.error or "PR creation returned failure"})
        except Exception as exc:
            logger.debug("Fallback PR creation raised", exc_info=True)
            ctx.state_mgr.update_metadata({"pr_error": str(exc)})

    if pr_number is None:
        pr_err = state.metadata.get("pr_error", "No pull request could be created or discovered.")
        ctx.state_mgr.update_metadata({"pr_failed": True, "pr_error": pr_err})
        ctx.emit("pr_creation_failed", {"iteration": iteration, "error": pr_err})
        ctx.state_mgr.transition_to(PipelineStatus.HITL_REVIEW_DECISION)
        ctx.emit("hitl_gate", {"gate": PipelineStatus.HITL_REVIEW_DECISION.value})
        return

    _cr_session = f"cr-{iteration}-{uuid.uuid4().hex[:8]}"
    _cr_cfg = getattr(ctx.config, "code_reviewer", None)
    _cr_provider = (getattr(_cr_cfg, "provider", None) or "openai") if _cr_cfg else "openai"
    if _cr_provider == "ollama":
        _cr_model = (getattr(_cr_cfg, "ollama_model", "") or "") if _cr_cfg else ""
    else:
        _cr_model = (getattr(_cr_cfg, "model", "") or "") if _cr_cfg else ""
    _cr_model = (_cr_model or ctx.config.codex.model_routing.get("CODE_REVIEWER")
                 or ctx.config.codex.default_model or "")
    ctx.emit_terminal("session_start", "CODE_REVIEWER", _cr_session,
                      iteration=iteration, module_id="", model=_cr_model)

    def _on_stdout(line: str) -> None:
        ctx.emit("reviewer_stdout", {"line": line})
        ctx.emit_terminal("line", "CODE_REVIEWER", _cr_session,
                          line=line, stream="stdout", iteration=iteration, module_id="")

    runner = CodeReviewerRunner(ctx.config, vcs_client=ctx.make_project_vcs())
    try:
        run_result = runner.run(
            pr_number=pr_number,
            iteration=iteration,
            feature_branch=feature_branch,
            on_stdout=_on_stdout,
        )
    except Exception as exc:
        logger.exception("Code reviewer raised: %s", exc)
        ctx.emit_terminal("session_end", "CODE_REVIEWER", _cr_session, exit_code=1)
        err_msg = str(exc)
        ctx.state_mgr.transition_to(
            PipelineStatus.HITL_REVIEW_DECISION,
            metadata={"code_review_failed": True, "code_review_error": err_msg},
        )
        ctx.emit("code_review_failed", {"iteration": iteration, "error": err_msg})
        ctx.emit("hitl_gate", {"gate": PipelineStatus.HITL_REVIEW_DECISION.value})
        return

    review = run_result.review
    ctx.state_mgr.update_metadata({
        "review_json_content": review.model_dump_json(),
        "review_json_path": run_result.review_json_path,
        "review_overall_status": review.overall_status,
        "review_overall_score": str(review.overall_score),
        "pr_merged": str(run_result.pr_merged),
        "branch_deleted": str(run_result.branch_deleted),
    })
    ctx.upsert_iteration(
        iteration,
        review_json_content=review.model_dump_json(),
        review_json_path=str(run_result.review_json_path),
    )

    ctx.emit_terminal("session_end", "CODE_REVIEWER", _cr_session, exit_code=0)
    ctx.emit("code_review_complete", {
        "iteration": iteration,
        "overall_status": review.overall_status,
        "overall_score": review.overall_score,
        "comments_posted": run_result.comments_posted,
        "pr_merged": run_result.pr_merged,
    })

    if review.overall_status == "accepted" and run_result.pr_merged:
        logger.info("Review accepted and PR merged — transitioning to PIPELINE_COMPLETE")
        ctx.state_mgr.transition_to(PipelineStatus.PIPELINE_COMPLETE)
        ctx.emit("pipeline_complete", {"reason": "accepted_by_reviewer", "iteration": iteration})
        return

    ctx.state_mgr.transition_to(PipelineStatus.HITL_REVIEW_DECISION)
    ctx.emit("hitl_gate", {"gate": PipelineStatus.HITL_REVIEW_DECISION.value})
