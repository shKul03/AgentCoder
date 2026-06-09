"""GitHub-review pipeline step functions (github_review mode)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Optional

from ...ado.client import AzureDevOpsClient
from ...storage.models import PipelineStatus
from ...vcs.factory import make_vcs_client
from .base import StepContext

logger = logging.getLogger(__name__)


def _resolve_ado_ids(ctx: StepContext) -> tuple[str, str, list[int]]:
    meta = ctx.state_mgr.state.metadata
    ado_org = meta.get("ado_org", "") or getattr(ctx.config.requirements, "ado_org", "")
    ado_token = meta.get("ado_token", "") or getattr(ctx.config.requirements, "ado_token", "")
    story_id = ctx.state_mgr.state.current_story_id
    if story_id and str(story_id).isdigit():
        work_item_ids: list[int] = [int(story_id)]
    else:
        work_item_ids = meta.get("ado_work_item_ids", [])
    return ado_org, ado_token, work_item_ids


def step_analyse_dependencies(ctx: StepContext) -> None:
    """ANALYSING_DEPENDENCIES → QUEUE_READY."""
    import asyncio
    from ...orchestrator.story_queue import StoryQueueManager

    logger.info("[GHR] Analysing story dependencies")
    ctx.emit("state_changed", {"step": "analyse_dependencies"})

    conn = ctx.db.conn
    story_rows = conn.execute(
        "SELECT id, title, description FROM requirements WHERE type = 'story'"
    ).fetchall()

    raw_stories = []
    for row in story_rows:
        ac_rows = conn.execute(
            "SELECT title FROM requirements WHERE parent_id = ? AND type = 'acceptance_criteria'",
            (row["id"],),
        ).fetchall()
        raw_stories.append({
            "story_id": row["id"],
            "title": row["title"],
            "description": row["description"] or "",
            "acceptance_criteria": [ac["title"] for ac in ac_rows],
        })

    if not raw_stories:
        logger.warning("[GHR] No stories found — completing pipeline")
        ctx.state_mgr.transition_to(PipelineStatus.PIPELINE_COMPLETE)
        ctx.emit("pipeline_complete", {"reason": "no_stories"})
        return

    logger.info("[GHR] Found %d stories — building queue", len(raw_stories))
    ctx.emit("analyse_dependencies_started", {"story_count": len(raw_stories)})

    mgr = StoryQueueManager(ctx.db)
    items = asyncio.run(mgr.build_queue(
        raw_stories,
        api_key=getattr(ctx.config.secrets, "openai_api_key", "") or "",
        model=getattr(ctx.config, "openai_model", "gpt-4o-mini") or "gpt-4o-mini",
    ))

    ctx.state_mgr.update_story_context(stories_total=len(items), stories_completed=0)
    logger.info("[GHR] Queue built — %d stories in execution order", len(items))
    ctx.emit("queue_built", {"stories": [
        {"story_id": it.story_id, "title": it.title, "position": it.position,
         "depends_on": it.depends_on}
        for it in items
    ]})
    ctx.state_mgr.transition_to(PipelineStatus.QUEUE_READY)
    ctx.emit("state_changed")


def step_queue_ready(ctx: StepContext, fork_and_clone: object) -> None:
    """QUEUE_READY → STORY_PROMPT_GENERATION (or PIPELINE_COMPLETE if queue empty)."""
    from ...orchestrator.story_queue import StoryQueueManager

    if not ctx.config.project.root_path:
        if not fork_and_clone():  # type: ignore[operator]
            return

    mgr = StoryQueueManager(ctx.db)
    next_story = mgr.dequeue()

    if next_story is None:
        counts = mgr.counts()
        logger.info("[GHR] Queue exhausted — %s", counts)
        ctx.state_mgr.transition_to(PipelineStatus.PIPELINE_COMPLETE)
        ctx.emit("pipeline_complete", {"reason": "queue_exhausted", "counts": counts})
        # Close all ADO items at final completion
        ado_org, ado_token, wi_ids = _resolve_ado_ids(ctx)
        if ado_org and ado_token and wi_ids:
            AzureDevOpsClient(ado_org, ado_token).close(wi_ids)
        return

    logger.info("[GHR] Starting story: %s — %s", next_story.story_id, next_story.title)
    ctx.state_mgr.update_story_context(current_story_id=next_story.story_id)
    ctx.emit("story_started", {
        "story_id": next_story.story_id,
        "title": next_story.title,
        "position": next_story.position,
    })
    ctx.state_mgr.update_metadata({"story_iteration": 1, "story_review_json": None})
    ctx.state_mgr.transition_to(PipelineStatus.STORY_PROMPT_GENERATION)
    ctx.emit("state_changed")


def step_story_prompt_generation(ctx: StepContext) -> None:
    """STORY_PROMPT_GENERATION → HITL_PROMPT_REVIEW."""
    from ...prompt_generator.runner import PromptGeneratorRunner
    from ...orchestrator.story_queue import StoryQueueManager

    state = ctx.state_mgr.state
    story_id = state.current_story_id
    story_iter = state.metadata.get("story_iteration", 1)

    logger.info("[GHR] Story prompt generation — story=%s iter=%d", story_id, story_iter)
    ctx.emit("prompt_generation_started", {"story_id": story_id, "story_iteration": story_iter})

    mgr = StoryQueueManager(ctx.db)
    item = mgr.get_item(story_id) if story_id else None
    if item is None:
        logger.error("[GHR] Could not find story %s — failing", story_id)
        ctx.state_mgr.transition_to(PipelineStatus.FAILED)
        return

    acs_md = "\n".join(f"- {ac}" for ac in item.acceptance_criteria) or "(no acceptance criteria)"
    requirements_text = (
        f"## Story: {item.title}\n\n"
        f"{item.description}\n\n"
        f"### Acceptance Criteria\n{acs_md}\n"
    )

    review_json: Optional[dict] = None
    if story_iter > 1:
        review_raw = state.metadata.get("story_review_json")
        if review_raw:
            try:
                review_json = json.loads(review_raw) if isinstance(review_raw, str) else review_raw
            except Exception:
                review_json = {"raw": str(review_raw)}

    _pg_session = f"pg-story-{story_id}-iter{story_iter}-{uuid.uuid4().hex[:8]}"
    _pg_cfg = getattr(ctx.config, "prompt_generator", None)
    _pg_provider = getattr(_pg_cfg, "provider", "ollama")
    if _pg_provider == "openai":
        model_pg = getattr(_pg_cfg, "openai_model", None) or "gpt-4.1-mini"
    else:
        model_pg = f"ollama/{getattr(_pg_cfg, 'ollama_model', None) or 'llama3.1:8b'}"
    ctx.emit_terminal("session_start", "PROMPT_GENERATOR", _pg_session,
                      story_id=story_id, iteration=story_iter, model=model_pg)

    def _on_stdout(line: str) -> None:
        ctx.emit("prompt_token", {"line": line})
        ctx.emit_terminal("token", "PROMPT_GENERATOR", _pg_session,
                          text=line, stream="stdout", story_id=story_id, iteration=story_iter)

    runner = PromptGeneratorRunner(ctx.config)
    try:
        prompt_text = runner.run(
            iteration=story_iter,
            requirements_text=requirements_text,
            review_json=review_json,
            on_stdout=_on_stdout,
            story_context={
                "story_id": story_id or "",
                "title": item.title,
                "pr_number": state.metadata.get("story_pr_number"),
                "is_fork_mode": True,
            },
        )
    except Exception as exc:
        logger.exception("[GHR] Story prompt generator failed: %s", exc)
        ctx.emit_terminal("session_end", "PROMPT_GENERATOR", _pg_session, exit_code=1)
        ctx.state_mgr.update_metadata({"story_prompt_error": str(exc)})
        ctx.state_mgr.transition_to(PipelineStatus.FAILED)
        ctx.emit("error", {"message": str(exc), "story_id": story_id})
        return

    ctx.state_mgr.update_metadata({"prompt_content": prompt_text, "story_prompt_error": ""})
    ctx.emit_terminal("session_end", "PROMPT_GENERATOR", _pg_session, exit_code=0)
    ctx.emit("prompt_generation_complete", {
        "story_id": story_id, "story_iteration": story_iter, "char_count": len(prompt_text),
    })
    ctx.state_mgr.transition_to(PipelineStatus.HITL_PROMPT_REVIEW)
    ctx.emit("hitl_gate", {"gate": PipelineStatus.HITL_PROMPT_REVIEW.value})


def step_story_code_generation(
    ctx: StepContext,
    provision_project_dir: object,  # callable() -> str
    active_codex_ref: list,
    code_gen_stop_requested: object,
) -> None:
    """STORY_CODE_GENERATION → STORY_CODE_REVIEW."""
    from ...code_generator.runner import CodeGeneratorRunner
    from ...orchestrator.story_queue import StoryQueueManager

    state = ctx.state_mgr.state
    story_id = state.current_story_id
    story_iter = state.metadata.get("story_iteration", 1)

    logger.info("[GHR] Story code generation — story=%s iter=%d", story_id, story_iter)

    if story_iter == 1:
        ado_org, ado_token, wi_ids = _resolve_ado_ids(ctx)
        if ado_org and ado_token and wi_ids:
            AzureDevOpsClient(ado_org, ado_token).activate(wi_ids)

    mgr = StoryQueueManager(ctx.db)
    item = mgr.get_item(story_id) if story_id else None

    if item and not item.branch_name:
        slug = re.sub(r"[^a-z0-9]+", "-", item.title.lower()).strip("-")[:40]
        branch_name = f"story-{story_id}-{slug}".lower()
        mgr.update_branch(story_id, branch_name)
    else:
        branch_name = (item.branch_name if item else None) or f"story-{story_id}"

    ctx.config.project.feature_branch = branch_name
    if story_iter == 1:
        ctx.state_mgr.update_metadata({"pr_number": None, "pr_url": ""})

    prompt_path = getattr(ctx.config.project, "prompt_file_path", "") or "data/prompts/latest.md"
    working_dir = getattr(ctx.config.project, "root_path", "") or ""
    if not working_dir:
        working_dir = provision_project_dir()  # type: ignore[operator]

    cli_tool = state.metadata.get("selected_cli_tool")
    if cli_tool:
        ctx.config.codex.cli_routing["CODE_GENERATOR"] = cli_tool
        logger.info("[GHR] CLI tool overridden to '%s'", cli_tool)
    cli_model = state.metadata.get("selected_cli_model")
    if cli_model:
        ctx.config.codex.model_routing["CODE_GENERATOR"] = cli_model

    pr_number: Optional[int] = None
    if story_iter > 1:
        pr_raw = state.metadata.get("pr_number")
        if pr_raw is not None:
            try:
                pr_number = int(pr_raw)
            except (TypeError, ValueError):
                pass

    _cg_session = f"cg-story-{story_id}-iter{story_iter}-{uuid.uuid4().hex[:8]}"
    _cg_model = ctx.config.codex.model_routing.get("CODE_GENERATOR") or ctx.config.codex.default_model or ""
    _cg_tool = cli_tool or ctx.config.codex.cli_routing.get("CODE_GENERATOR", "codex")
    ctx.emit_terminal("session_start", "CODE_GENERATOR", _cg_session,
                      story_id=story_id, iteration=story_iter, model=_cg_model, tool=_cg_tool)
    ctx.emit("code_generation_started", {"story_id": story_id, "story_iteration": story_iter})

    def _on_stdout(line: str) -> None:
        ctx.emit("codex_stdout", {"line": line})
        ctx.emit_terminal("line", "CODE_GENERATOR", _cg_session,
                          line=line, stream="stdout", story_id=story_id)

    def _on_stderr(line: str) -> None:
        ctx.emit("codex_stderr", {"line": line})
        ctx.emit_terminal("line", "CODE_GENERATOR", _cg_session,
                          line=line, stream="stderr", story_id=story_id)

    runner = CodeGeneratorRunner(ctx.config, vcs_client=make_vcs_client(ctx.config))
    active_codex_ref[0] = runner._codex
    code_gen_stop_requested.clear()  # type: ignore[attr-defined]
    try:
        gen_result = runner.run(
            prompt_path=prompt_path,
            working_dir=working_dir,
            iteration=story_iter,
            pr_number=pr_number,
            on_stdout=_on_stdout,
            on_stderr=_on_stderr,
            story_context={
                "story_id": story_id or "",
                "title": item.title if item else "",
                "acceptance_criteria": item.acceptance_criteria if item else [],
            } if item else None,
        )
    except Exception as exc:
        active_codex_ref[0] = None
        logger.exception("[GHR] Story code generator raised: %s", exc)
        ctx.emit_terminal("session_end", "CODE_GENERATOR", _cg_session, exit_code=1)
        if code_gen_stop_requested.is_set():  # type: ignore[attr-defined]
            ctx.state_mgr.transition_to(
                PipelineStatus.CODE_GEN_STOPPED,
                metadata={"stopped_working_dir": str(working_dir), "stopped_story_id": story_id or ""},
            )
            ctx.emit("code_gen_stopped", {"story_id": story_id})
        else:
            ctx.state_mgr.transition_to(
                PipelineStatus.CODE_GEN_FAILED,
                metadata={"code_gen_failed": True, "code_gen_error": str(exc)},
            )
            ctx.emit("code_gen_failed", {"story_id": story_id, "error": str(exc)})
        return
    active_codex_ref[0] = None

    if code_gen_stop_requested.is_set():  # type: ignore[attr-defined]
        ctx.emit_terminal("session_end", "CODE_GENERATOR", _cg_session, exit_code=-2)
        ctx.state_mgr.transition_to(
            PipelineStatus.CODE_GEN_STOPPED,
            metadata={"stopped_working_dir": str(working_dir), "stopped_story_id": story_id or ""},
        )
        ctx.emit("code_gen_stopped", {"story_id": story_id, "working_dir": str(working_dir)})
        return

    meta_update: dict = {"completion_status": gen_result.completion.status.value}
    if gen_result.pr_number is not None:
        meta_update["pr_number"] = gen_result.pr_number
        meta_update["pr_url"] = gen_result.pr_url
    if gen_result.git_errors:
        meta_update["git_errors"] = gen_result.git_errors
    ctx.state_mgr.update_metadata(meta_update)

    if gen_result.git_errors:
        for git_err in gen_result.git_errors:
            logger.warning("[GHR] Git error for story %s: %s", story_id, git_err)
            ctx.emit_terminal("line", "CODE_GENERATOR", _cg_session,
                              line=f"[git] {git_err}", stream="stderr", story_id=story_id)

    _is_incomplete = gen_result.completion.status.value in ("failed", "partial")
    ctx.emit_terminal("session_end", "CODE_GENERATOR", _cg_session,
                      exit_code=0 if not _is_incomplete else 1)

    if _is_incomplete:
        err = gen_result.completion.reason or "Code generation did not complete successfully."
        logger.warning("[GHR] Story %s code gen incomplete: %s", story_id, err)
        ctx.state_mgr.transition_to(
            PipelineStatus.CODE_GEN_FAILED,
            metadata={"code_gen_failed": True, "code_gen_error": err},
        )
        ctx.emit("code_gen_failed", {"story_id": story_id, "error": err})
        return

    if gen_result.pr_number is None and gen_result.git_errors:
        err_git = "; ".join(gen_result.git_errors)
        logger.warning("[GHR] Story %s git ops failed — no PR created: %s", story_id, err_git)
        ctx.state_mgr.transition_to(
            PipelineStatus.CODE_GEN_FAILED,
            metadata={"code_gen_failed": True, "code_gen_error": f"Git ops failed: {err_git}"},
        )
        ctx.emit("code_gen_failed", {"story_id": story_id, "error": err_git})
        return

    ctx.emit("code_generation_complete", {
        "story_id": story_id,
        "story_iteration": story_iter,
        "pr_number": gen_result.pr_number,
        "pr_url": gen_result.pr_url,
    })
    ctx.state_mgr.transition_to(PipelineStatus.STORY_CODE_REVIEW)
    ctx.emit("state_changed")


def step_story_code_review(ctx: StepContext, resume_in_thread: object) -> None:
    """STORY_CODE_REVIEW → STORY_COMPLETE (accepted) or loops back."""
    from ...code_reviewer.runner import CodeReviewerRunner
    from ...orchestrator.story_queue import StoryQueueManager

    state = ctx.state_mgr.state
    story_id = state.current_story_id
    story_iter = state.metadata.get("story_iteration", 1)
    max_story_iters = getattr(ctx.config.orchestrator, "max_story_iterations",
                              ctx.config.orchestrator.max_iterations)

    logger.info("[GHR] Story code review — story=%s iter=%d", story_id, story_iter)
    ctx.emit("code_review_started", {"story_id": story_id, "story_iteration": story_iter})

    pr_number: Optional[int] = None
    pr_raw = state.metadata.get("pr_number")
    if pr_raw is not None:
        try:
            pr_number = int(pr_raw)
        except (TypeError, ValueError):
            pass

    feature_branch = ctx.config.project.feature_branch or f"story-{story_id}"

    if pr_number is None:
        try:
            vcs = ctx.make_project_vcs()
            if vcs:
                pr_number = vcs.find_open_pr(feature_branch)
                if pr_number:
                    ctx.state_mgr.update_metadata({"pr_number": pr_number})
        except Exception:
            logger.debug("[GHR] PR discovery failed", exc_info=True)

    if pr_number is None:
        err_no_pr = "No pull request found — code may not have been pushed to GitHub."
        logger.warning("[GHR] No PR for story %s", story_id)
        ctx.state_mgr.transition_to(
            PipelineStatus.CODE_GEN_FAILED,
            metadata={"code_gen_failed": True, "code_gen_error": err_no_pr},
        )
        ctx.emit("code_gen_failed", {"story_id": story_id, "reason": "no_pr", "error": err_no_pr})
        return

    _cr_session = f"cr-story-{story_id}-iter{story_iter}-{uuid.uuid4().hex[:8]}"
    _cr_cfg = getattr(ctx.config, "code_reviewer", None)
    _cr_provider = (getattr(_cr_cfg, "provider", None) or "openai") if _cr_cfg else "openai"
    if _cr_provider == "ollama":
        _cr_model = (getattr(_cr_cfg, "ollama_model", "") or "") if _cr_cfg else ""
    else:
        _cr_model = (getattr(_cr_cfg, "model", "") or "") if _cr_cfg else ""
    _cr_model = (_cr_model or ctx.config.codex.model_routing.get("CODE_REVIEWER")
                 or ctx.config.codex.default_model or "")
    ctx.emit_terminal("session_start", "CODE_REVIEWER", _cr_session,
                      story_id=story_id, iteration=story_iter, model=_cr_model)

    def _on_stdout(line: str) -> None:
        ctx.emit("reviewer_stdout", {"line": line})
        ctx.emit_terminal("line", "CODE_REVIEWER", _cr_session,
                          line=line, stream="stdout", story_id=story_id)

    mgr = StoryQueueManager(ctx.db)
    _item = mgr.get_item(story_id) if story_id else None
    runner = CodeReviewerRunner(ctx.config, vcs_client=ctx.make_project_vcs())
    try:
        run_result = runner.run(
            pr_number=pr_number,
            iteration=story_iter,
            feature_branch=feature_branch,
            on_stdout=_on_stdout,
            story_context={
                "story_id": story_id or "",
                "title": (_item.title if _item else ""),
                "acceptance_criteria": (_item.acceptance_criteria if _item else []),
            },
        )
    except Exception as exc:
        logger.exception("[GHR] Story code reviewer raised: %s", exc)
        ctx.emit_terminal("session_end", "CODE_REVIEWER", _cr_session, exit_code=1)
        err_msg = str(exc)
        ctx.state_mgr.transition_to(
            PipelineStatus.HITL_REVIEW_DECISION,
            metadata={"code_review_failed": True, "code_review_error": err_msg},
        )
        ctx.emit("code_review_failed", {"story_id": story_id, "error": err_msg})
        ctx.emit("hitl_gate", {"gate": PipelineStatus.HITL_REVIEW_DECISION.value})
        return

    review = run_result.review
    review_json_str = review.model_dump_json()
    ctx.state_mgr.update_metadata({
        "story_review_json": review_json_str,
        "review_json_content": review_json_str,
        "review_overall_status": review.overall_status,
        "pr_merged": str(run_result.pr_merged),
    })
    ctx.upsert_iteration(story_iter, review_json_content=review_json_str)

    if run_result.pr_merged:
        StoryQueueManager(ctx.db).mark_complete(
            story_id, pr_number=pr_number, pr_url=state.metadata.get("pr_url", "")
        )

    ctx.emit_terminal("session_end", "CODE_REVIEWER", _cr_session, exit_code=0)
    ctx.emit("code_review_complete", {
        "story_id": story_id,
        "story_iteration": story_iter,
        "overall_status": review.overall_status,
        "overall_score": review.overall_score,
        "pr_merged": run_result.pr_merged,
    })

    if review.overall_status == "accepted":
        logger.info("[GHR] Story %s accepted (iter %d)", story_id, story_iter)
        ctx.state_mgr.update_story_context(stories_completed=state.stories_completed + 1)
        ctx.state_mgr.transition_to(PipelineStatus.STORY_COMPLETE)
        ctx.emit("story_completed", {"story_id": story_id, "story_iteration": story_iter})
    elif story_iter >= max_story_iters:
        logger.warning("[GHR] Story %s hit max iterations (%d)", story_id, max_story_iters)
        StoryQueueManager(ctx.db).mark_failed(story_id, reason=f"Max iterations ({max_story_iters}) reached")
        ctx.state_mgr.update_story_context(stories_completed=state.stories_completed + 1)
        ctx.state_mgr.transition_to(PipelineStatus.STORY_COMPLETE)
        ctx.emit("story_failed", {"story_id": story_id, "reason": "max_iterations"})
    else:
        next_iter = story_iter + 1
        logger.info("[GHR] Story %s rejected — looping to iteration %d", story_id, next_iter)
        ctx.state_mgr.update_metadata({"story_iteration": next_iter})
        StoryQueueManager(ctx.db).increment_iteration(story_id)
        if ctx.config.orchestrator.auto_approve_hitl:
            ctx.state_mgr.transition_to(PipelineStatus.STORY_PROMPT_GENERATION)
            ctx.emit("state_changed", {"story_id": story_id, "next_story_iteration": next_iter})
            resume_in_thread()  # type: ignore[operator]
        else:
            ctx.state_mgr.transition_to(PipelineStatus.HITL_REVIEW_DECISION)
            ctx.emit("hitl_gate", {
                "gate": PipelineStatus.HITL_REVIEW_DECISION.value,
                "story_id": story_id,
                "next_story_iteration": next_iter,
                "review_overall_status": review.overall_status,
            })


def step_story_complete(ctx: StepContext) -> None:
    """STORY_COMPLETE → QUEUE_READY or PIPELINE_COMPLETE."""
    from ...orchestrator.story_queue import StoryQueueManager

    state = ctx.state_mgr.state
    story_id = state.current_story_id
    mgr = StoryQueueManager(ctx.db)

    logger.info("[GHR] Story complete: %s", story_id)

    # Close this story's ADO work item (Active → Closed)
    ado_org, ado_token, wi_ids = _resolve_ado_ids(ctx)
    if ado_org and ado_token and wi_ids:
        AzureDevOpsClient(ado_org, ado_token).close(wi_ids)

    if mgr.is_complete():
        counts = mgr.counts()
        logger.info("[GHR] All stories processed — %s", counts)
        ctx.state_mgr.transition_to(PipelineStatus.PIPELINE_COMPLETE)
        ctx.emit("pipeline_complete", {
            "reason": "all_stories_processed",
            "stories_completed": state.stories_completed,
            "stories_total": state.stories_total,
            "counts": counts,
        })
    else:
        ctx.state_mgr.transition_to(PipelineStatus.QUEUE_READY)
        ctx.emit("state_changed", {"next": "queue_ready"})
