"""Orchestrator API routes — /api/orchestrator/*"""

from __future__ import annotations

import threading
import logging
from typing import Optional

import json as _json
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import get_orchestrator, orch_holder
from ..schemas import (
    ApproveGateRequest,
    ApproveGateResponse,
    ApprovePromptRequest,
    CurrentPromptResponse,
    CurrentReviewResponse,
    IterationListResponse,
    IterationResponse,
    OrchestratorStatusResponse,
    StoryQueueDetailResponse,
    StoryQueueReorderRequest,
)
from ...orchestrator.engine import Orchestrator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


@router.get("/status", response_model=OrchestratorStatusResponse)
def get_status(orch: Orchestrator = Depends(get_orchestrator)):
    """Return the current pipeline status."""
    state = orch.state_mgr.state
    return OrchestratorStatusResponse(
        pipeline_status=state.pipeline_status.value,
        current_iteration=state.current_iteration,
        last_checkpoint=state.last_checkpoint,
        metadata=state.metadata,
        is_hitl_gate=orch.state_mgr.is_hitl_gate(),
        mode=getattr(orch.config, "pipeline_mode", "standard") or "standard",
        current_story_id=getattr(state, "current_story_id", None),
        stories_completed=getattr(state, "stories_completed", 0),
        stories_total=getattr(state, "stories_total", 0),
    )


@router.post("/start", response_model=ApproveGateResponse)
def start_pipeline(orch: Orchestrator = Depends(get_orchestrator)):
    """Start (or resume) the pipeline in a background thread.

    If the pipeline is in a terminal state (PIPELINE_COMPLETE or FAILED) it is
    automatically reset to IDLE before starting.  If it is stuck at
    CODE_GEN_FAILED the code-generator retry is triggered instead of spawning
    a duplicate thread.
    """
    status = orch.state_mgr.current_status
    from ...storage.models import PipelineStatus
    if status in (PipelineStatus.PIPELINE_COMPLETE, PipelineStatus.FAILED):
        orch.reset()
    elif status == PipelineStatus.CODE_GEN_FAILED:
        # Resume from a code-gen timeout/failure — retry rather than starting
        # a new thread that would immediately break at the CODE_GEN_FAILED guard.
        ok = orch.retry_code_generator()
        if ok:
            return ApproveGateResponse(approved=True, message="Code generation retry started")
        # retry_code_generator returned False (race condition) — fall through
        # and let the normal thread start path handle it gracefully.
    t = threading.Thread(target=orch.run, daemon=True, name="orchestrator-start")
    t.start()
    return ApproveGateResponse(approved=True, message="Pipeline started")


@router.post("/approve-prompt", response_model=ApproveGateResponse)
def approve_prompt(
    body: ApprovePromptRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """Approve the generated prompt and optionally supply an edited version."""
    ok = orch.approve_prompt(
        prompt_content=body.prompt_content,
        cli_tool=body.cli_tool,
        cli_model=body.cli_model,
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Pipeline is not waiting at HITL_PROMPT_REVIEW gate.",
        )
    return ApproveGateResponse(approved=True, message="Prompt approved — pipeline resumed")


@router.post("/approve-review", response_model=ApproveGateResponse)
def approve_review(orch: Orchestrator = Depends(get_orchestrator)):
    """Approve the code review and continue (loop or complete)."""
    ok = orch.approve_review()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Pipeline is not waiting at HITL_REVIEW_DECISION gate.",
        )
    return ApproveGateResponse(approved=True, message="Review approved — pipeline resumed")


@router.post("/move-to-next-story", response_model=ApproveGateResponse)
def move_to_next_story(orch: Orchestrator = Depends(get_orchestrator)):
    """Merge the current story's PR, delete its branch, then advance to the next story.

    This action skips the normal approve/reject loop: it force-merges the PR
    regardless of review score, marks the current story as complete, and resumes
    the pipeline from the queue.  If the queue is exhausted the pipeline
    transitions to PIPELINE_COMPLETE.

    Only available when the pipeline is paused at HITL_REVIEW_DECISION (i.e.
    after the code reviewer has produced a review JSON for the current iteration).
    """
    ok = orch.move_to_next_story()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Cannot move to next story — pipeline is not at HITL_REVIEW_DECISION.",
        )
    return ApproveGateResponse(approved=True, message="Merging PR and advancing to next story")


@router.post("/approve-gate", response_model=ApproveGateResponse)
def approve_gate(
    body: ApproveGateRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """Generic gate approval — kept for backward compatibility."""
    ok = orch.approve_gate(gate=body.gate)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Could not approve gate — pipeline is not at the expected gate state.",
        )
    return ApproveGateResponse(approved=True, message="Gate approved")


@router.post("/pause", response_model=ApproveGateResponse)
def pause_pipeline(orch: Orchestrator = Depends(get_orchestrator)):
    """Request the pipeline to pause after the current step."""
    orch.pause()
    return ApproveGateResponse(approved=True, message="Pause requested")


@router.post("/stop", response_model=ApproveGateResponse)
def stop_code_generation(orch: Orchestrator = Depends(get_orchestrator)):
    """Kill the active code-generation subprocess mid-flight.

    Sends a kill signal to the running Codex/Aider/Claude process, preserves
    whatever file changes have been written to disk so far, and transitions the
    pipeline to CODE_GEN_STOPPED.  The user must then choose one of:
      - POST /stop-rollback  — discard partial changes and return to HITL_PROMPT_REVIEW
      - POST /stop-continue  — commit & push partial changes then proceed to code review

    Returns 409 if the pipeline is not currently in a code-generation state.
    """
    ok = orch.stop_code_generation()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Cannot stop — pipeline is not currently in a code-generation state.",
        )
    return ApproveGateResponse(approved=True, message="Stop signal sent — killing code generation subprocess")


@router.post("/stop-rollback", response_model=ApproveGateResponse)
def stop_rollback(orch: Orchestrator = Depends(get_orchestrator)):
    """Roll back partial code-gen changes after a stop.

    Performs ``git reset --hard HEAD`` + ``git clean -fd`` in the project
    working directory to restore the codebase to the state it was in before
    code generation started, then returns the pipeline to HITL_PROMPT_REVIEW.

    Only valid when the pipeline is at CODE_GEN_STOPPED.
    """
    ok = orch.rollback_after_stop()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Cannot roll back — pipeline is not at CODE_GEN_STOPPED.",
        )
    return ApproveGateResponse(approved=True, message="Partial changes discarded — returned to prompt review")


@router.post("/stop-continue", response_model=ApproveGateResponse)
def stop_continue(orch: Orchestrator = Depends(get_orchestrator)):
    """Save partial code-gen progress and continue to code review.

    Commits whatever files were written before the stop, pushes the feature
    branch to GitHub, creates (or finds) a pull request, then advances the
    pipeline to CODE_REVIEW (standard mode) or STORY_CODE_REVIEW (GHR mode).

    If no file changes are detected, the pipeline rolls back to
    HITL_PROMPT_REVIEW automatically.

    Only valid when the pipeline is at CODE_GEN_STOPPED.
    """
    ok = orch.continue_after_stop()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Cannot continue — pipeline is not at CODE_GEN_STOPPED.",
        )
    return ApproveGateResponse(approved=True, message="Partial changes committed — proceeding to code review")


@router.post("/retry-pr", response_model=ApproveGateResponse)
def retry_pr(orch: Orchestrator = Depends(get_orchestrator)):
    """Retry pull request creation after a failure."""
    ok = orch.retry_pr()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Cannot retry PR — pipeline is not in the expected state or retry failed.",
        )
    return ApproveGateResponse(approved=True, message="PR retry initiated — pipeline resuming")


@router.post("/retry-prompt-generator", response_model=ApproveGateResponse)
def retry_prompt_generator(orch: Orchestrator = Depends(get_orchestrator)):
    """Retry prompt generation after a failure."""
    ok = orch.retry_prompt_generator()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Cannot retry prompt generator — not at HITL_PROMPT_REVIEW with a failure.",
        )
    return ApproveGateResponse(approved=True, message="Prompt generator retry started")


@router.post("/retry-code-generator", response_model=ApproveGateResponse)
def retry_code_generator(
    body: ApprovePromptRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """Retry code generation after a failure, optionally with a different tool/model."""
    ok = orch.retry_code_generator(
        cli_tool=body.cli_tool or "",
        cli_model=body.cli_model or "",
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Cannot retry code generator — not at CODE_GEN_FAILED state.",
        )
    return ApproveGateResponse(approved=True, message="Code generator retry started")


@router.post("/retry-code-reviewer", response_model=ApproveGateResponse)
def retry_code_reviewer(orch: Orchestrator = Depends(get_orchestrator)):
    """Retry code review after a failure."""
    ok = orch.retry_code_reviewer()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Cannot retry code reviewer — not at HITL_REVIEW_DECISION with a failure.",
        )
    return ApproveGateResponse(approved=True, message="Code reviewer retry started")


@router.post("/reset", response_model=ApproveGateResponse)
def reset_pipeline(orch: Orchestrator = Depends(get_orchestrator)):
    """Reset the pipeline to IDLE, discarding in-progress state.

    Also creates a new, version-incremented project folder so that old
    generated files are no longer visible in Projects / Code Insights.
    """
    orch.reset()

    # ── Rotate project folder ────────────────────────────────────────────
    import re
    from pathlib import Path

    old_root = getattr(orch.config.project, "root_path", "") or ""
    if old_root:
        old_path = Path(old_root)
        name = old_path.name  # e.g. "my-app_v5"
        parent = old_path.parent

        # Try to bump _vN suffix; otherwise append _v1
        m = re.search(r"_v(\d+)$", name)
        if m:
            ver = int(m.group(1)) + 1
            new_name = name[: m.start()] + f"_v{ver}"
        else:
            new_name = f"{name}_v1"

        new_path = parent / new_name
        new_path.mkdir(parents=True, exist_ok=True)

        orch.config.project.root_path = str(new_path)

        # Persist updated root_path to config.yaml
        from ..routes.settings import _write_config_yaml
        _write_config_yaml(orch.config, orch_holder.config_path)

        return ApproveGateResponse(
            approved=True,
            message=f"Pipeline reset to IDLE — new project folder: {new_path.name}",
        )

    return ApproveGateResponse(approved=True, message="Pipeline reset to IDLE")


@router.get("/iterations", response_model=IterationListResponse)
def get_iterations(orch: Orchestrator = Depends(get_orchestrator)):
    """Return all iteration records."""
    rows = orch.get_iterations()
    items = []
    for row in rows:
        try:
            items.append(IterationResponse(**row))
        except Exception:
            logger.debug("Skipping malformed iteration row: %s", row, exc_info=True)
    return IterationListResponse(iterations=items)


@router.get("/current-prompt", response_model=CurrentPromptResponse)
def get_current_prompt(orch: Orchestrator = Depends(get_orchestrator)):
    """Return the latest generated prompt (from state metadata or last iteration)."""
    state = orch.state_mgr.state
    prompt_content: str = state.metadata.get("prompt_content", "") or ""
    prompt_path: str = getattr(orch.config.project, "prompt_file_path", "") or ""
    iteration: int = state.current_iteration or 0

    # Fallback: try reading from the file path if metadata is empty
    if not prompt_content and prompt_path:
        from pathlib import Path as _Path
        try:
            prompt_content = _Path(prompt_path).read_text(encoding="utf-8")
        except Exception:
            pass

    return CurrentPromptResponse(
        iteration=iteration,
        content=prompt_content,
        path=prompt_path,
    )


@router.get("/current-review", response_model=CurrentReviewResponse)
def get_current_review(orch: Orchestrator = Depends(get_orchestrator)):
    """Return the latest code review JSON (from state metadata)."""
    state = orch.state_mgr.state
    review_content: str = state.metadata.get("review_json_content", "") or ""
    iteration: int = max(0, (state.current_iteration or 1) - 1)

    return CurrentReviewResponse(
        iteration=iteration,
        content=review_content,
        path="",
    )


@router.get("/bus-history")
def get_bus_history(
    channel: str = Query(..., description="Channel name: 'review_feedback' or 'validation_results'"),
    orch: Orchestrator = Depends(get_orchestrator),
) -> List[Any]:
    """Return historical BusMessage-shaped records for a given channel."""
    from datetime import datetime, timezone

    rows = orch.db.conn.execute(
        "SELECT iteration_number, review_json_content, ci_result, status "
        "FROM iterations ORDER BY iteration_number ASC"
    ).fetchall()

    messages: List[dict] = []
    for row in rows:
        iteration = row["iteration_number"]
        ts = datetime.now(timezone.utc).isoformat()

        if channel == "review_feedback":
            raw = (row["review_json_content"] or "").strip()
            if not raw:
                continue
            try:
                payload = _json.loads(raw)
            except _json.JSONDecodeError:
                payload = {"raw": raw}
            messages.append({
                "channel": "review_feedback",
                "sender": "code_reviewer",
                "timestamp": ts,
                "module_id": "code_reviewer",
                "iteration": iteration,
                "payload": payload,
            })

        elif channel == "validation_results":
            raw = (row["ci_result"] or "").strip()
            if not raw:
                continue
            try:
                payload = _json.loads(raw)
            except _json.JSONDecodeError:
                payload = {"raw": raw, "status": row["status"]}
            messages.append({
                "channel": "validation_results",
                "sender": "ci_runner",
                "timestamp": ts,
                "module_id": "ci_runner",
                "iteration": iteration,
                "payload": payload,
            })

    return messages


@router.get("/story-queue")
def get_story_queue(orch: Orchestrator = Depends(get_orchestrator)):
    """Return the current story queue state (GitHub Review mode).

    Returns an empty stories list when not in github_review mode.
    """
    from ...orchestrator.story_queue import StoryQueueManager

    mgr = StoryQueueManager(orch.db)
    stories = mgr.get_queue_state()
    state = orch.state_mgr.state
    return {
        "mode": getattr(orch.config, "pipeline_mode", "standard") or "standard",
        "current_story_id": getattr(state, "current_story_id", None),
        "stories_completed": getattr(state, "stories_completed", 0),
        "stories_total": getattr(state, "stories_total", 0),
        "stories": stories,
    }


@router.get("/story-queue/{story_id}", response_model=StoryQueueDetailResponse)
def get_story_queue_item(
    story_id: str,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """Return a single story-queue item by story_id."""
    from ...orchestrator.story_queue import StoryQueueManager

    mgr = StoryQueueManager(orch.db)
    item = mgr.get_item(story_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Story '{story_id}' not found in queue.")
    # mode='json' converts enums→strings and datetimes→ISO strings
    return StoryQueueDetailResponse(**item.model_dump(mode="json"))


@router.post("/story-queue/reorder", response_model=ApproveGateResponse)
def reorder_story_queue(
    body: StoryQueueReorderRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """Manually reorder the story queue.

    Accepts a list of story_ids in the desired order; the first element
    becomes position 0. Only QUEUED stories may be reordered — IN_PROGRESS,
    COMPLETED, and FAILED stories are not moved.
    """
    from ...orchestrator.story_queue import StoryQueueManager

    mgr = StoryQueueManager(orch.db)
    queue = mgr.get_queue_state()

    # Build new position map from the requested ordering
    id_to_pos = {sid: idx for idx, sid in enumerate(body.story_ids)}

    conn = orch.db.conn
    updated = 0
    for row in queue:
        sid = row["story_id"]
        if sid in id_to_pos and row["status"] == "queued":
            conn.execute(
                "UPDATE story_queue SET position = ? WHERE story_id = ?",
                (id_to_pos[sid], sid),
            )
            updated += 1
    conn.commit()
    return ApproveGateResponse(
        approved=True,
        message=f"Reordered {updated} queued stories.",
    )


# ---------------------------------------------------------------------------
# CLI tool routing
# ---------------------------------------------------------------------------

class _SetCliToolRequest(BaseModel):
    post: str  # e.g. "CODE_GENERATOR"
    tool: str  # e.g. "claude"


@router.put("/cli-tool")
def set_cli_tool(
    body: _SetCliToolRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """Persist which CLI tool handles a given agent post."""
    from ...codex.cli_adapter import SUPPORTED_TOOLS

    post = body.post.upper()
    tool = body.tool.lower()

    _VALID_POSTS = {"CODE_GENERATOR", "PROMPT_GENERATOR", "CODE_REVIEWER"}
    if post not in _VALID_POSTS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid post '{post}'. Valid values: {sorted(_VALID_POSTS)}",
        )
    if tool not in SUPPORTED_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tool '{tool}'. Supported: {SUPPORTED_TOOLS}",
        )

    orch.config.codex.cli_routing[post] = tool

    if orch_holder.config_path:
        from ..routes.settings import _write_config_yaml
        _write_config_yaml(orch.config, orch_holder.config_path)

    return {
        "post": post,
        "tool": tool,
        "cli_routing": dict(orch.config.codex.cli_routing),
    }


# ---------------------------------------------------------------------------
# Review JSON override (HITL edit before approve)
# ---------------------------------------------------------------------------

class _UpdateReviewRequest(BaseModel):
    content: str  # raw ReviewJSON string


@router.put("/review")
def update_review(
    body: _UpdateReviewRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """Overwrite the pending review JSON before the user hits approve-review.

    Validates the content as a ReviewJSON, then stores it in pipeline metadata
    so that ``approve_review`` picks up the updated version.
    """
    import json as _json_mod
    from ...code_reviewer.schema import ReviewJSON

    raw = body.content.strip()
    if not raw:
        raise HTTPException(status_code=422, detail="content must not be empty")

    try:
        data = _json_mod.loads(raw)
        review = ReviewJSON.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid ReviewJSON: {exc}")

    validated_json = review.model_dump_json()
    orch.state_mgr.update_metadata({
        "review_json_content": validated_json,
        "review_overall_status": review.overall_status,
    })

    return {
        "iteration": orch.state_mgr.state.current_iteration or 0,
        "overall_status": review.overall_status,
        "content": validated_json,
    }
