"""Orchestrator engine — slim coordination layer.

State machine:
  IDLE → LOADING_REQUIREMENTS → PROMPT_GENERATION → HITL_PROMPT_REVIEW
       → CODE_GENERATION → CODE_REVIEW → HITL_REVIEW_DECISION
       → PROMPT_GENERATION (loop) ... → PIPELINE_COMPLETE

Step logic lives in:
  orchestrator/steps/standard.py    — standard pipeline steps
  orchestrator/steps/github_review.py — github_review pipeline steps
"""

from __future__ import annotations

import asyncio
import logging
import re
import stat
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from ..config.schema import AgentOSConfig
from ..storage.database import Database
from ..storage.models import PipelineState, PipelineStatus
from .state import StateManager
from .steps.base import StepContext
from .steps import standard as _std
from .steps import github_review as _ghr

logger = logging.getLogger(__name__)
console = Console()


class Orchestrator:
    """Autonomous SDLC pipeline orchestrator."""

    def __init__(self, config: AgentOSConfig) -> None:
        self.config = config
        self.db = Database(config.storage.db_path)
        self.db.connect()
        self.state_mgr = StateManager(self.db)

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._code_gen_stop_requested = threading.Event()
        self._active_codex_wrapper = None
        self._loop_thread: Optional[threading.Thread] = None

        self._ws_queue: Optional[asyncio.Queue] = None

        self.state_mgr.on_transition(self._on_state_transition)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _on_state_transition(
        self,
        old_status: PipelineStatus,
        new_status: PipelineStatus,
        new_state: Any,
    ) -> None:
        iter_num: int = new_state.current_iteration
        if not iter_num:
            return
        if new_status == PipelineStatus.FAILED:
            self._upsert_iteration(iter_num, status="failed",
                                   completed_at=datetime.utcnow().isoformat())
        elif new_status == PipelineStatus.PIPELINE_COMPLETE:
            self._upsert_iteration(iter_num, status="completed",
                                   completed_at=datetime.utcnow().isoformat())

    def _upsert_iteration(self, iteration_number: int, **kwargs: Any) -> None:
        try:
            count = self.db.conn.execute(
                "SELECT COUNT(*) FROM iterations WHERE iteration_number = ?",
                (iteration_number,),
            ).fetchone()[0]
            if count == 0:
                cols = ["iteration_number", "status", "started_at"] + list(kwargs.keys())
                vals: list[Any] = [
                    iteration_number,
                    kwargs.pop("status", "in_progress"),
                    kwargs.pop("started_at", datetime.utcnow().isoformat()),
                    *kwargs.values(),
                ]
                placeholders = ", ".join("?" for _ in vals)
                self.db.conn.execute(
                    f"INSERT INTO iterations ({', '.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
            else:
                if not kwargs:
                    return
                set_clause = ", ".join(f"{k} = ?" for k in kwargs)
                self.db.conn.execute(
                    f"UPDATE iterations SET {set_clause} WHERE iteration_number = ?",
                    [*kwargs.values(), iteration_number],
                )
            self.db.conn.commit()
        except Exception:
            logger.debug("_upsert_iteration(%d) failed", iteration_number, exc_info=True)

    def _make_step_context(self) -> StepContext:
        active_ref = [self._active_codex_wrapper]  # mutable ref for step functions
        return StepContext(
            config=self.config,
            db=self.db,
            state_mgr=self.state_mgr,
            ws_queue=self._ws_queue,
            _emit_fn=self._emit,
            _emit_terminal_fn=self._emit_terminal,
            _upsert_iteration_fn=self._upsert_iteration,
        )

    # ── WebSocket integration ──────────────────────────────────────────────────

    def set_ws_queue(self, queue: asyncio.Queue) -> None:
        self._ws_queue = queue

    def _emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._ws_queue is None:
            return
        import datetime as _dt
        payload = {
            "channel": "pipeline",
            "sender": "orchestrator",
            "event": event_type,
            "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
            "pipeline_status": self.state_mgr.current_status.value,
            "current_iteration": self.state_mgr.state.current_iteration,
            **(data or {}),
        }
        try:
            self._ws_queue.put_nowait(payload)
        except Exception:
            pass

    def _emit_terminal(
        self,
        event_type: str,
        agent_post: str,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        if self._ws_queue is None:
            return
        import datetime as _dt
        payload = {
            "channel": f"terminal:{agent_post.lower()}",
            "sender": agent_post.lower(),
            "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
            "payload": {
                "event_type": event_type,
                "agent_post": agent_post,
                "session_id": session_id,
                **kwargs,
            },
        }
        try:
            self._ws_queue.put_nowait(payload)
        except Exception:
            pass

    # ── Main run loop ──────────────────────────────────────────────────────────

    def run(self) -> None:
        self._stop_event.clear()
        self._pause_event.clear()
        state = self.state_mgr.state
        logger.info("Pipeline run() called — current status: %s", state.pipeline_status.value)
        self._emit("run_started")
        self._loop()

    def _loop(self) -> None:
        ctx = self._make_step_context()
        active_codex_ref: list = [None]

        _STANDARD_DISPATCH = {
            PipelineStatus.IDLE:                 lambda: _std.step_idle(ctx),
            PipelineStatus.LOADING_REQUIREMENTS: lambda: _std.step_load_requirements(ctx),
            PipelineStatus.PROMPT_GENERATION:    lambda: _std.step_prompt_generation(ctx),
            PipelineStatus.CODE_GENERATION:      lambda: _std.step_code_generation(
                ctx, self._provision_project_dir, active_codex_ref, self._code_gen_stop_requested
            ),
            PipelineStatus.CODE_REVIEW:          lambda: _std.step_code_review(ctx),
        }
        _GHR_DISPATCH = {
            PipelineStatus.IDLE:                    lambda: _std.step_idle(ctx),
            PipelineStatus.LOADING_REQUIREMENTS:    lambda: _std.step_load_requirements(ctx),
            PipelineStatus.ANALYSING_DEPENDENCIES:  lambda: _ghr.step_analyse_dependencies(ctx),
            PipelineStatus.QUEUE_READY:             lambda: _ghr.step_queue_ready(ctx, self._fork_and_clone),
            PipelineStatus.STORY_PROMPT_GENERATION: lambda: _ghr.step_story_prompt_generation(ctx),
            PipelineStatus.STORY_CODE_GENERATION:   lambda: _ghr.step_story_code_generation(
                ctx, self._provision_project_dir, active_codex_ref, self._code_gen_stop_requested
            ),
            PipelineStatus.STORY_CODE_REVIEW:       lambda: _ghr.step_story_code_review(ctx, self._resume_in_thread),
            PipelineStatus.STORY_COMPLETE:          lambda: _ghr.step_story_complete(ctx),
        }
        _is_ghr = (self.config.pipeline_mode == "github_review")
        if not _is_ghr:
            logger.warning(
                "pipeline_mode is '%s' — this project targets github_review mode. "
                "Standard mode is supported but not the primary workflow.",
                self.config.pipeline_mode,
            )
        _DISPATCH = _GHR_DISPATCH if _is_ghr else _STANDARD_DISPATCH

        while True:
            if self._stop_event.is_set():
                logger.info("Pipeline stopped (reset requested)")
                self._emit("stopped")
                break

            if self._pause_event.is_set():
                logger.info("Pipeline paused by user")
                self._emit("paused")
                break

            status = self.state_mgr.current_status

            if status == PipelineStatus.PIPELINE_COMPLETE:
                logger.info("Pipeline complete!")
                self._emit("pipeline_complete")
                self._close_ado_work_items()
                break

            if status == PipelineStatus.FAILED:
                logger.warning("Pipeline in FAILED state — awaiting reset")
                self._emit("failed")
                break

            if status == PipelineStatus.CODE_GEN_FAILED:
                logger.warning("Pipeline paused at CODE_GEN_FAILED — awaiting retry or reset")
                break

            if status == PipelineStatus.CODE_GEN_STOPPED:
                logger.info("Pipeline stopped by user — awaiting rollback or continue decision")
                self._emit("code_gen_stopped", {
                    "working_dir": self.state_mgr.state.metadata.get("stopped_working_dir", ""),
                })
                break

            if self.state_mgr.is_hitl_gate():
                if self.config.orchestrator.auto_approve_hitl:
                    logger.info("Auto-approving HITL gate: %s", status.value)
                    self._auto_approve(status)
                    continue
                logger.info("Paused at HITL gate: %s", status.value)
                self._emit("hitl_gate", {"gate": status.value})
                break

            step = _DISPATCH.get(status)
            if step is None:
                logger.error("No step handler for state: %s", status.value)
                self.state_mgr.transition_to(PipelineStatus.FAILED)
                break

            # Keep active_codex_ref in sync with the wrapper slot used by steps
            self._active_codex_wrapper = active_codex_ref[0]
            try:
                step()
            except Exception as exc:
                logger.exception("Error in step %s: %s", status.value, exc)
                self.state_mgr.transition_to(PipelineStatus.FAILED)
                self._emit("error", {"message": str(exc)})
                break
            finally:
                self._active_codex_wrapper = active_codex_ref[0]

    # ── Fork + clone (GHR only) ───────────────────────────────────────────────

    def _fork_and_clone(self) -> bool:
        """Fork the source repo and clone it locally (once per pipeline run).

        Returns True on success or graceful skip; False on hard failure
        (state is already transitioned to FAILED in that case).
        """
        import time as _time

        cfg = self.config
        source_url = getattr(cfg.github_review, "source_repo_url", "") or ""

        if not source_url:
            logger.info("[GHR] No source_repo_url — skipping fork+clone")
            return True

        m = re.match(
            r"https?://github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?/?$",
            source_url,
        )
        if not m:
            err = f"[GHR] Cannot parse source_repo_url: {source_url!r}"
            logger.error(err)
            self.state_mgr.transition_to(PipelineStatus.FAILED, metadata={"error": err})
            self._emit("error", {"message": err})
            return False

        source_owner, source_repo = m.groups()
        token = getattr(cfg.secrets, "github_token", "") or ""
        owner = getattr(cfg.github, "owner", "") or ""
        if not token or not owner:
            logger.warning("[GHR] GitHub token or owner not configured — skipping fork+clone")
            return True

        fork_name = getattr(cfg.github_review, "fork_repo_name", "") or f"{source_repo}-agent-os"

        from ..vcs.github_client import GitHubVCSClient
        from ..git_ops.manager import GitOpsManager

        vcs = GitHubVCSClient(token=token, owner=owner, repo=fork_name)
        same_owner = source_owner.lower() == owner.lower()

        if same_owner:
            logger.info("[GHR] source_owner == owner (%s) — skipping fork", owner)
            self._emit("fork_skipped", {"reason": "same_owner", "repo": f"{source_owner}/{source_repo}"})
        else:
            self._emit("fork_started", {"source": f"{source_owner}/{source_repo}",
                                        "fork": f"{owner}/{fork_name}"})
            fork_result = vcs.fork_repo(source_owner, source_repo, name=fork_name)
            if not fork_result.success:
                err_lower = (fork_result.error or "").lower()
                already = any(kw in err_lower for kw in ("already exists", "422", "409"))
                if not already:
                    if "not found" in err_lower or fork_result.status_code == 404:
                        err = (
                            f"Fork failed: source repo '{source_owner}/{source_repo}' not found "
                            f"(404). Check source_repo_url and GitHub token scope."
                        )
                    else:
                        err = f"Fork failed: {fork_result.error}"
                    logger.error("[GHR] %s", err)
                    self.state_mgr.transition_to(PipelineStatus.FAILED, metadata={"error": err})
                    self._emit("error", {"message": err})
                    return False
                logger.info("[GHR] Fork already exists — continuing")
            self._emit("fork_waiting", {"fork": f"{owner}/{fork_name}"})
            if not vcs.wait_for_fork(owner, fork_name, max_wait_seconds=30):
                logger.warning("[GHR] Fork %s/%s not ready after 30s — proceeding anyway", owner, fork_name)

        clone_repo_owner = source_owner if same_owner else owner
        clone_repo_name = source_repo if same_owner else fork_name
        clone_target = Path.home() / "Desktop" / clone_repo_name

        if clone_target.exists() and (clone_target / ".git").is_dir():
            logger.info("[GHR] Fork already cloned at %s — reusing", clone_target)
        else:
            clone_vcs = GitHubVCSClient(token=token, owner=clone_repo_owner, repo=clone_repo_name)
            clone_url = clone_vcs.get_remote_url(clone_repo_name)
            self._emit("clone_started", {
                "url": f"github.com/{clone_repo_owner}/{clone_repo_name}",
                "target": str(clone_target),
            })
            clone_result, _ = GitOpsManager.clone_and_open(clone_url, clone_target)
            if not clone_result.success:
                err = f"Clone failed: {clone_result.stderr}"
                logger.error("[GHR] %s", err)
                self.state_mgr.transition_to(PipelineStatus.FAILED, metadata={"error": err})
                self._emit("error", {"message": err})
                return False

        git = GitOpsManager(str(clone_target))
        git.set_user("Agent OS Bot", "agent-os@noreply.github.com")
        self.config.project.root_path = str(clone_target)
        self.config.project.repo_name = clone_repo_name
        self.state_mgr.update_metadata({
            "fork_name": clone_repo_name,
            "fork_owner": clone_repo_owner,
            "fork_clone_path": str(clone_target),
        })

        logger.info("[GHR] Fork+clone complete → %s", clone_target)
        self._emit("fork_clone_complete", {
            "repo": f"{clone_repo_owner}/{clone_repo_name}",
            "local_path": str(clone_target),
        })
        return True

    # ── ADO helpers ───────────────────────────────────────────────────────────

    def _resolve_ado(self) -> tuple[str, str, list[int]]:
        meta = self.state_mgr.state.metadata
        ado_org = meta.get("ado_org", "") or getattr(self.config.requirements, "ado_org", "")
        ado_token = meta.get("ado_token", "") or getattr(self.config.requirements, "ado_token", "")
        story_id = self.state_mgr.state.current_story_id
        if story_id and str(story_id).isdigit():
            wi_ids: list[int] = [int(story_id)]
        else:
            wi_ids = meta.get("ado_work_item_ids", [])
        return ado_org, ado_token, wi_ids

    def _activate_ado_work_items(self) -> None:
        from ..ado.client import AzureDevOpsClient
        ado_org, ado_token, wi_ids = self._resolve_ado()
        if ado_org and ado_token and wi_ids:
            AzureDevOpsClient(ado_org, ado_token).activate(wi_ids)
        else:
            logger.debug("[ADO] No credentials or IDs — skipping New→Active transition")

    def _close_ado_work_items(self, story_id: Optional[str] = None) -> None:
        from ..ado.client import AzureDevOpsClient
        meta = self.state_mgr.state.metadata
        ado_org = meta.get("ado_org", "") or getattr(self.config.requirements, "ado_org", "")
        ado_token = meta.get("ado_token", "") or getattr(self.config.requirements, "ado_token", "")
        if not ado_org or not ado_token:
            logger.debug("[ADO] No credentials — skipping Active→Closed transition")
            return
        if story_id and str(story_id).isdigit():
            wi_ids: list[int] = [int(story_id)]
        else:
            wi_ids = meta.get("ado_work_item_ids", [])
        if wi_ids:
            AzureDevOpsClient(ado_org, ado_token).close(wi_ids)

    # ── HITL gate approvals ───────────────────────────────────────────────────

    def _auto_approve(self, status: PipelineStatus) -> None:
        is_ghr = getattr(self.config, "pipeline_mode", "") == "github_review"
        if status == PipelineStatus.HITL_PROMPT_REVIEW:
            next_status = PipelineStatus.STORY_CODE_GENERATION if is_ghr else PipelineStatus.CODE_GENERATION
            self.state_mgr.transition_to(next_status)
        elif status == PipelineStatus.HITL_REVIEW_DECISION:
            if is_ghr:
                self.state_mgr.transition_to(PipelineStatus.STORY_PROMPT_GENERATION)
            else:
                self.state_mgr.transition_to(PipelineStatus.PIPELINE_COMPLETE)
        self._emit("state_changed")

    def approve_prompt(
        self,
        prompt_content: Optional[str] = None,
        cli_tool: Optional[str] = None,
        cli_model: Optional[str] = None,
    ) -> bool:
        if self.state_mgr.current_status != PipelineStatus.HITL_PROMPT_REVIEW:
            logger.warning("approve_prompt() called but not at HITL_PROMPT_REVIEW")
            return False

        metadata: dict[str, Any] = {}
        if cli_tool:
            metadata["selected_cli_tool"] = cli_tool
        if cli_model:
            metadata["selected_cli_model"] = cli_model
        if prompt_content is not None:
            metadata["edited_prompt"] = prompt_content
            prompt_path = getattr(self.config.project, "prompt_file_path", "")
            if prompt_path:
                try:
                    Path(prompt_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(prompt_path).write_text(prompt_content, encoding="utf-8")
                    logger.info("Edited prompt saved to: %s", prompt_path)
                except Exception:
                    logger.warning("Could not save prompt to file", exc_info=True)

        if metadata:
            self.state_mgr.update_metadata(metadata)

        is_ghr = getattr(self.config, "pipeline_mode", "") == "github_review"
        next_status = PipelineStatus.STORY_CODE_GENERATION if is_ghr else PipelineStatus.CODE_GENERATION
        self.state_mgr.transition_to(next_status)
        self._emit("state_changed", {"approved": "prompt"})
        self._resume_in_thread()
        return True

    def approve_review(self) -> bool:
        if self.state_mgr.current_status != PipelineStatus.HITL_REVIEW_DECISION:
            logger.warning("approve_review() called but not at HITL_REVIEW_DECISION")
            return False

        state = self.state_mgr.state
        review_status = state.metadata.get("review_overall_status", "")
        if review_status == "accepted":
            is_ghr = getattr(self.config, "pipeline_mode", "") == "github_review"
            if is_ghr:
                self.state_mgr.transition_to(PipelineStatus.STORY_COMPLETE)
                self._emit("story_completed", {"story_id": state.current_story_id, "reason": "user_approved"})
                self._resume_in_thread()
            else:
                self.state_mgr.transition_to(PipelineStatus.PIPELINE_COMPLETE)
                self._emit("pipeline_complete", {"reason": "user_approved_accepted_review"})
            return True

        is_ghr = getattr(self.config, "pipeline_mode", "") == "github_review"
        if is_ghr:
            self.state_mgr.transition_to(PipelineStatus.STORY_PROMPT_GENERATION)
            self._emit("state_changed", {
                "approved": "review",
                "next": "story_prompt_generation",
                "story_id": state.current_story_id,
            })
            self._resume_in_thread()
        else:
            max_iter = self.config.orchestrator.max_iterations
            if state.current_iteration >= max_iter:
                self.state_mgr.transition_to(PipelineStatus.PIPELINE_COMPLETE)
                self._emit("pipeline_complete", {"reason": "max_iterations_reached"})
            else:
                self.state_mgr.transition_to(
                    PipelineStatus.PROMPT_GENERATION,
                    iteration=state.current_iteration + 1,
                )
                self._emit("state_changed", {
                    "approved": "review",
                    "next_iteration": state.current_iteration + 1,
                })
                self._resume_in_thread()

        return True

    def move_to_next_story(self) -> bool:
        """Merge current PR, delete branch, mark story complete, then resume."""
        from ..orchestrator.story_queue import StoryQueueManager

        if self.state_mgr.current_status != PipelineStatus.HITL_REVIEW_DECISION:
            logger.warning("move_to_next_story() called but pipeline is not at HITL_REVIEW_DECISION")
            return False

        state = self.state_mgr.state
        story_id = state.current_story_id
        feature_branch = self.config.project.feature_branch or (f"story-{story_id}" if story_id else "dev")

        pr_number: Optional[int] = None
        pr_raw = state.metadata.get("pr_number")
        if pr_raw is not None:
            try:
                pr_number = int(pr_raw)
            except (TypeError, ValueError):
                pass

        ctx = self._make_step_context()
        vcs = ctx.make_project_vcs()
        merged = False
        branch_deleted = False
        comments_resolved = False

        if vcs and pr_number:
            try:
                resolve_results = vcs.resolve_all_pr_review_comments(pr_number)
                comments_resolved = all(r.success for r in resolve_results) if resolve_results else True
                logger.info("[GHR] move_to_next_story: resolved %d threads on PR #%d",
                            len(resolve_results), pr_number)
            except Exception:
                logger.warning("[GHR] move_to_next_story: resolve comments raised", exc_info=True)

            try:
                merge_result = vcs.merge_pr(
                    pr_number,
                    commit_message=f"Story {story_id}: merged via Agent OS — Move to Next Story",
                )
                merged = merge_result.success
                if not merged:
                    logger.warning("[GHR] PR #%d merge failed: %s", pr_number, merge_result.error)
            except Exception:
                logger.warning("[GHR] move_to_next_story: merge PR raised", exc_info=True)

        if vcs and feature_branch:
            try:
                del_result = vcs.delete_branch(feature_branch)
                branch_deleted = del_result.success
            except Exception:
                logger.debug("[GHR] move_to_next_story: branch delete raised", exc_info=True)

        mgr = StoryQueueManager(self.db)
        mgr.mark_complete(story_id, pr_number=pr_number, pr_url=state.metadata.get("pr_url", ""))

        self.state_mgr.update_story_context(stories_completed=state.stories_completed + 1)
        self.state_mgr.update_metadata({
            "pr_merged": str(merged),
            "branch_deleted": str(branch_deleted),
            "comments_resolved": str(comments_resolved),
            "move_to_next_story_triggered": "true",
        })

        self._close_ado_work_items(story_id=story_id)

        self.state_mgr.transition_to(PipelineStatus.STORY_COMPLETE)
        self._emit("story_completed", {
            "story_id": story_id,
            "reason": "move_to_next_story",
            "pr_merged": merged,
            "branch_deleted": branch_deleted,
        })
        self._resume_in_thread()
        return True

    def pause(self) -> bool:
        self._pause_event.set()
        logger.info("Pause requested")
        return True

    def stop_code_generation(self) -> bool:
        from ..codex.session import SessionType

        status = self.state_mgr.current_status
        if status not in (PipelineStatus.CODE_GENERATION, PipelineStatus.STORY_CODE_GENERATION):
            logger.warning("stop_code_generation() called but not in a code-generation state (%s)",
                           status.value)
            return False

        self._code_gen_stop_requested.set()
        wrapper = self._active_codex_wrapper
        killed = False
        if wrapper is not None:
            killed = wrapper.kill_session(SessionType.CODE_GENERATOR)
            logger.info("stop_code_generation: kill_session = %s", killed)
        else:
            logger.warning("stop_code_generation: no active wrapper found")

        self._emit("code_gen_stopping", {
            "message": "Stop requested — killing code generation subprocess",
            "killed": killed,
        })
        return True

    def rollback_after_stop(self) -> bool:
        from ..git_ops.manager import GitOpsManager

        if self.state_mgr.current_status != PipelineStatus.CODE_GEN_STOPPED:
            logger.warning("rollback_after_stop() called but not at CODE_GEN_STOPPED (current: %s)",
                           self.state_mgr.current_status.value)
            return False

        state = self.state_mgr.state
        working_dir = (
            state.metadata.get("stopped_working_dir", "")
            or getattr(self.config.project, "root_path", "")
            or ""
        )

        if working_dir:
            wd = Path(working_dir)
            if wd.exists():
                git = GitOpsManager(str(wd))
                r_reset = git.reset_hard("HEAD")
                if not r_reset.success:
                    logger.warning("rollback_after_stop: reset --hard failed: %s", r_reset.stderr)
                r_clean = git._run("clean", "-fd")
                if not r_clean.success:
                    logger.warning("rollback_after_stop: clean -fd failed: %s", r_clean.stderr)
            else:
                logger.warning("rollback_after_stop: working_dir does not exist: %s", working_dir)

        self.state_mgr.update_metadata({"stopped_working_dir": "", "code_gen_stopped": False})
        self.state_mgr.transition_to(PipelineStatus.HITL_PROMPT_REVIEW)
        self._emit("hitl_gate", {
            "gate": PipelineStatus.HITL_PROMPT_REVIEW.value,
            "reason": "stop_rollback",
        })
        return True

    def continue_after_stop(self) -> bool:
        from pathlib import Path as _Path
        from ..code_generator.runner import CodeGeneratorRunner, CodeGenResult
        from ..code_generator.completion import CompletionResult, CompletionStatus
        from ..codex.session import CodexResult
        from ..git_ops.manager import GitOpsManager
        from ..vcs.factory import make_vcs_client

        if self.state_mgr.current_status != PipelineStatus.CODE_GEN_STOPPED:
            logger.warning("continue_after_stop() called but not at CODE_GEN_STOPPED (current: %s)",
                           self.state_mgr.current_status.value)
            return False

        state = self.state_mgr.state
        working_dir = (
            state.metadata.get("stopped_working_dir", "")
            or getattr(self.config.project, "root_path", "")
            or ""
        )
        if not working_dir:
            logger.error("continue_after_stop: no working_dir available")
            return False

        wd = _Path(working_dir)
        git = GitOpsManager(str(wd))

        if not git.has_changes():
            logger.info("continue_after_stop: no changes — rolling back to HITL_PROMPT_REVIEW")
            self.state_mgr.update_metadata({"stopped_working_dir": "", "code_gen_stopped": False})
            self.state_mgr.transition_to(PipelineStatus.HITL_PROMPT_REVIEW)
            self._emit("hitl_gate", {
                "gate": PipelineStatus.HITL_PROMPT_REVIEW.value,
                "reason": "stop_continue_no_changes",
                "message": "No changes were written — rolled back to prompt review.",
            })
            return True

        is_ghr = (self.config.pipeline_mode == "github_review")
        story_id = state.current_story_id
        iteration = state.metadata.get("story_iteration", 1) if is_ghr else state.current_iteration
        pr_number: Optional[int] = None
        pr_raw = state.metadata.get("pr_number")
        if pr_raw is not None:
            try:
                pr_number = int(pr_raw)
            except (TypeError, ValueError):
                pass

        syn_result = CodeGenResult(
            completion=CompletionResult(status=CompletionStatus.COMPLETE, reason="partial — user stopped"),
            codex_result=CodexResult(exit_code=0, stdout="", stderr=""),
        )

        runner = CodeGeneratorRunner(self.config, vcs_client=make_vcs_client(self.config))
        self._emit("code_gen_continue_started", {
            "working_dir": working_dir, "iteration": iteration, "story_id": story_id,
        })

        if is_ghr:
            from ..orchestrator.story_queue import StoryQueueManager
            _sq = StoryQueueManager(self.db)
            _item = _sq.get_item(story_id) if story_id else None
            git_errors = runner._git_operations_fork_mode(
                wd, iteration, pr_number, syn_result,
                story_context={
                    "story_id": story_id or "",
                    "title": _item.title if _item else "",
                    "acceptance_criteria": _item.acceptance_criteria if _item else [],
                },
            )
        else:
            git_errors = runner._git_operations(wd, iteration, pr_number, syn_result)

        meta_update: dict = {"code_gen_stopped": False, "stopped_working_dir": ""}
        if syn_result.pr_number is not None:
            meta_update["pr_number"] = syn_result.pr_number
            meta_update["pr_url"] = syn_result.pr_url
        if git_errors:
            meta_update["git_errors"] = git_errors
            logger.warning("continue_after_stop: git errors: %s", git_errors)
        self.state_mgr.update_metadata(meta_update)

        next_status = PipelineStatus.STORY_CODE_REVIEW if is_ghr else PipelineStatus.CODE_REVIEW
        self.state_mgr.transition_to(next_status)
        self._emit("state_changed", {
            "reason": "stop_continue",
            "next": next_status.value,
            "pr_number": syn_result.pr_number,
        })
        self._resume_in_thread()
        return True

    def retry_pr(self) -> bool:
        if self.state_mgr.current_status != PipelineStatus.HITL_REVIEW_DECISION:
            logger.warning("retry_pr() called but not at HITL_REVIEW_DECISION (current: %s)",
                           self.state_mgr.current_status.value)
            return False

        state = self.state_mgr.state
        if not state.metadata.get("pr_failed"):
            logger.warning("retry_pr() called but pr_failed is not set in metadata")
            return False

        from ..git_ops.manager import GitOpsManager

        iteration = state.current_iteration
        feature_branch = self.config.project.feature_branch or "dev"
        repo_name = self.config.project.repo_name or ""

        def _persist_error(err: str) -> None:
            self.state_mgr.update_metadata({"pr_error": err})

        working_dir = Path(self.config.project.root_path) if self.config.project.root_path else None
        if not working_dir or not working_dir.exists():
            err = f"Project root_path does not exist: {working_dir}"
            self._emit("pr_retry_progress", {"step": "error", "message": err})
            _persist_error(err)
            return True

        token = getattr(self.config.secrets, "github_token", "") or ""
        owner = getattr(self.config.github, "owner", "") or ""
        effective_repo = repo_name or getattr(self.config.github, "repo", "") or ""

        if not token or not owner:
            from ..vcs.factory import make_vcs_client
            vcs = make_vcs_client(self.config)
        else:
            from ..vcs.github_client import GitHubVCSClient
            vcs = GitHubVCSClient(token=token, owner=owner, repo=effective_repo) if effective_repo else None

        if vcs is None:
            err = "VCS client not configured (check GitHub token / owner / repo_name in Settings)"
            self._emit("pr_retry_progress", {"step": "error", "message": err})
            _persist_error(err)
            return True

        if effective_repo and hasattr(vcs, "for_repo") and effective_repo != getattr(vcs, "_repo", ""):
            vcs = vcs.for_repo(effective_repo)

        try:
            _existing_pr = vcs.find_open_pr(feature_branch)
            if _existing_pr is not None:
                self.state_mgr.update_metadata({"pr_number": _existing_pr, "pr_failed": False, "pr_error": ""})
                self._emit("pr_retry_progress", {
                    "step": "create_pr",
                    "message": f"Found existing pull request #{_existing_pr}",
                })
                self._emit("pr_retry_success", {"pr_number": _existing_pr, "iteration": iteration})
                self.state_mgr.transition_to(PipelineStatus.CODE_REVIEW)
                self._resume_in_thread()
                return True
        except Exception:
            pass

        self._emit("pr_retry_progress", {"step": "create_repo", "message": "Creating repository..."})
        if effective_repo:
            create_result = vcs.create_repo(effective_repo)
            if create_result.success:
                self._emit("pr_retry_progress", {"step": "create_repo", "message": "Repository created"})
            else:
                err_lower = (create_result.error or "").lower()
                if any(kw in err_lower for kw in ("already exists", "name already", "422", "409")):
                    self._emit("pr_retry_progress", {"step": "create_repo", "message": "Repository already exists"})
                else:
                    err = f"Create repo failed: {create_result.error}"
                    self._emit("pr_retry_progress", {"step": "error", "message": err})
                    _persist_error(err)
                    return True

        self._emit("pr_retry_progress", {"step": "push", "message": "Pushing code..."})
        git = GitOpsManager(working_dir)
        remote_url = vcs.get_remote_url(effective_repo)
        _BOT_NAME = "Agent OS"
        _BOT_EMAIL = "agent-os@automated.dev"

        if not git.is_repo():
            r = git.init_repo()
            if not r.success:
                err = f"git init failed: {r.stderr}"
                self._emit("pr_retry_progress", {"step": "error", "message": err})
                _persist_error(err)
                return True
            git.set_user(_BOT_NAME, _BOT_EMAIL)
            git.add_remote("origin", remote_url)
        else:
            git.add_remote("origin", remote_url)

        git.commit_all(f"Agent OS iteration {iteration}")
        r = git.push_upstream("main")
        if not r.success:
            r = git.push("main", force=True)
            if not r.success:
                err = f"Push main failed: {r.stderr}"
                self._emit("pr_retry_progress", {"step": "error", "message": err})
                _persist_error(err)
                return True

        r = git.checkout(feature_branch)
        if not r.success:
            r = git.create_and_checkout(feature_branch, "main")
            if not r.success:
                err = f"Create feature branch failed: {r.stderr}"
                self._emit("pr_retry_progress", {"step": "error", "message": err})
                _persist_error(err)
                return True

        r = git.push_upstream(feature_branch)
        if not r.success:
            r = git.push(feature_branch, force=True)
            if not r.success:
                err = f"Push feature branch failed: {r.stderr}"
                self._emit("pr_retry_progress", {"step": "error", "message": err})
                _persist_error(err)
                return True

        self._emit("pr_retry_progress", {"step": "push", "message": "Code pushed successfully"})

        self._emit("pr_retry_progress", {"step": "create_pr", "message": "Creating pull request..."})
        pr_result = vcs.create_pr(
            title=f"[Agent OS] Iteration {iteration} — implementation",
            head=feature_branch,
            base="main",
            body=f"Automated pull request created by Agent OS.\n\n**Iteration:** {iteration}\n",
        )
        if pr_result.success and pr_result.data:
            pr_number = pr_result.data.get("number")
            self.state_mgr.update_metadata({"pr_number": pr_number, "pr_failed": False, "pr_error": ""})
            self._emit("pr_retry_progress", {"step": "create_pr",
                                             "message": f"Pull request #{pr_number} created"})
            self._emit("pr_retry_success", {"pr_number": pr_number, "iteration": iteration})
            self.state_mgr.transition_to(PipelineStatus.CODE_REVIEW)
            self._resume_in_thread()
            return True
        else:
            err = pr_result.error or "Unknown error"
            err_lower = err.lower()
            if any(kw in err_lower for kw in ("already exists", "there is already", "pull request already", "422")):
                try:
                    existing = vcs.find_open_pr(feature_branch)
                    if existing is not None:
                        self.state_mgr.update_metadata({"pr_number": existing, "pr_failed": False, "pr_error": ""})
                        self._emit("pr_retry_success", {"pr_number": existing, "iteration": iteration})
                        self.state_mgr.transition_to(PipelineStatus.CODE_REVIEW)
                        self._resume_in_thread()
                        return True
                except Exception:
                    pass
            err_full = f"PR creation failed: {err}"
            self._emit("pr_retry_progress", {"step": "error", "message": err_full})
            _persist_error(err_full)
            return True

    def retry_prompt_generator(self) -> bool:
        if self.state_mgr.current_status != PipelineStatus.HITL_PROMPT_REVIEW:
            logger.warning("retry_prompt_generator() called but not at HITL_PROMPT_REVIEW")
            return False
        if not self.state_mgr.state.metadata.get("prompt_gen_failed"):
            logger.warning("retry_prompt_generator() called but prompt_gen_failed not set")
            return False
        self.state_mgr.transition_to(
            PipelineStatus.PROMPT_GENERATION,
            metadata={"prompt_gen_failed": False, "prompt_gen_error": ""},
        )
        self._emit("state_changed", {"retry": "prompt_generator"})
        self._resume_in_thread()
        return True

    def retry_code_generator(self, cli_tool: str = "", cli_model: str = "") -> bool:
        if self.state_mgr.current_status != PipelineStatus.CODE_GEN_FAILED:
            logger.warning("retry_code_generator() called but not at CODE_GEN_FAILED")
            return False
        if cli_tool:
            self.config.codex.cli_routing["CODE_GENERATOR"] = cli_tool
            self.state_mgr.update_metadata({"selected_cli_tool": cli_tool})
        if cli_model:
            self.config.codex.model_routing["CODE_GENERATOR"] = cli_model
            self.state_mgr.update_metadata({"selected_cli_model": cli_model})
        is_ghr = getattr(self.config, "pipeline_mode", "") == "github_review"
        next_status = PipelineStatus.STORY_CODE_GENERATION if is_ghr else PipelineStatus.CODE_GENERATION
        self.state_mgr.transition_to(next_status, metadata={"code_gen_failed": False, "code_gen_error": ""})
        self._emit("state_changed", {"retry": "code_generator"})
        self._resume_in_thread()
        return True

    def retry_code_reviewer(self) -> bool:
        if self.state_mgr.current_status != PipelineStatus.HITL_REVIEW_DECISION:
            logger.warning("retry_code_reviewer() called but not at HITL_REVIEW_DECISION")
            return False
        _is_ghr = (self.config.pipeline_mode == "github_review")
        _review_status = PipelineStatus.STORY_CODE_REVIEW if _is_ghr else PipelineStatus.CODE_REVIEW
        self.state_mgr.transition_to(
            _review_status,
            metadata={"code_review_failed": False, "code_review_error": ""},
        )
        self._emit("state_changed", {"retry": "code_reviewer"})
        self._resume_in_thread()
        return True

    def reset(self) -> None:
        self._stop_event.set()
        self._pause_event.set()
        self._code_gen_stop_requested.clear()
        self._active_codex_wrapper = None
        self.db.clear_run_data()
        self.config.project.name = ""
        self.config.project.root_path = ""
        self.config.project.repo_name = ""
        self.state_mgr.reset()
        logger.info("Pipeline reset to IDLE — iteration history cleared")
        self._emit("reset")

    def _provision_project_dir(self) -> str:
        """Create and return a project folder under ~/Desktop."""
        raw_name = (self.config.project.name or "").strip()
        if not raw_name:
            try:
                import yaml as _yaml
                req_path = getattr(self.config.requirements, "path", "")
                if req_path:
                    req_file = Path(req_path)
                    text = req_file.read_text(encoding="utf-8")
                    if req_file.suffix.lower() == ".md":
                        match = re.search(r"```yaml\s*\n(.*?)\n```", text, re.DOTALL)
                        raw = _yaml.safe_load(match.group(1)) if match else {}
                    else:
                        raw = _yaml.safe_load(text)
                    epics = (raw or {}).get("epics", [])
                    if epics:
                        raw_name = epics[0].get("title", "").strip()
            except Exception:
                pass
        if not raw_name:
            raw_name = "agent-os-project"

        slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", raw_name).strip("-")
        slug = re.sub(r"-{2,}", "-", slug)[:60] or "agent-os-project"

        desktop = Path.home() / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)

        candidate = desktop / slug
        counter = 2
        while candidate.exists():
            candidate = desktop / f"{slug}-{counter}"
            counter += 1

        candidate.mkdir(parents=True, exist_ok=True)
        candidate.chmod(
            stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
        )
        logger.info("Project directory provisioned: %s", candidate)

        self.config.project.root_path = str(candidate)
        if not self.config.project.name:
            self.config.project.name = slug
        return str(candidate)

    def _resume_in_thread(self) -> None:
        self._stop_event.clear()
        self._pause_event.clear()
        if self._loop_thread is not None and self._loop_thread.is_alive():
            logger.warning("_resume_in_thread: loop thread already running — skipping")
            return
        self._loop_thread = threading.Thread(target=self._loop, daemon=True, name="orchestrator-loop")
        self._loop_thread.start()

    # ── Status helpers ────────────────────────────────────────────────────────

    def approve_gate(self, gate: Optional[str] = None) -> bool:
        """Generic gate approval — kept for backward compatibility with old pipeline routes."""
        status = self.state_mgr.current_status
        if gate:
            try:
                expected = PipelineStatus(gate)
                if status != expected:
                    return False
            except ValueError:
                return False
        if status == PipelineStatus.HITL_PROMPT_REVIEW:
            return self.approve_prompt()
        if status == PipelineStatus.HITL_REVIEW_DECISION:
            return self.approve_review()
        return False

    @property
    def current_iteration(self) -> int:
        return self.state_mgr.state.current_iteration

    def get_iterations(self) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            """SELECT id, iteration_number, status, prompt_path, prompt_content,
                      review_json_path, review_json_content, token_usage,
                      cli_tool_used, ci_result, ci_output, started_at, completed_at
               FROM iterations ORDER BY iteration_number ASC"""
        ).fetchall()
        return [dict(row) for row in rows]

    def get_current_prompt(self) -> str:
        prompt_path = getattr(self.config.project, "prompt_file_path", "")
        if prompt_path:
            p = Path(prompt_path)
            if p.exists():
                return p.read_text(encoding="utf-8")
        row = self.db.conn.execute(
            "SELECT prompt_content FROM iterations ORDER BY iteration_number DESC LIMIT 1"
        ).fetchone()
        return row["prompt_content"] if row else ""

    def get_current_review(self) -> str:
        row = self.db.conn.execute(
            "SELECT review_json_content FROM iterations ORDER BY iteration_number DESC LIMIT 1"
        ).fetchone()
        return row["review_json_content"] if row else ""

    def shutdown(self) -> None:
        self._stop_event.set()
        self.db.close()
