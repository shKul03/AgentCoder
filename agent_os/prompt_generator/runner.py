"""Prompt Generator runner — generates implementation/fix prompts via OpenAI API.

Iteration 1   : Generates a comprehensive implementation prompt from raw requirements.
Iteration 2+  : Generates a fixes-only prompt from the code reviewer's review JSON.

All LLM output streams line-by-line to the ``on_stdout`` callback for real-time
UI display in the Terminal Hub / Command Center.

The generated prompt is written to the fixed path configured in
``config.project.prompt_file_path`` (falls back to ``data/prompts/latest.md``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

from ..config.schema import AgentOSConfig

logger = logging.getLogger(__name__)


class PromptGeneratorRunner:
    """Generate implementation or fix prompts via OpenAI API streaming."""

    # ── System prompts ────────────────────────────────────────────────────────

    _SYSTEM_IMPLEMENTATION = """\
You are an expert prompt engineer specialising in prompts for autonomous AI \
coding agents (Codex, Claude Code, Gemini CLI, and similar tools).

Your task is to produce a comprehensive, actionable **implementation prompt** \
that an AI coding agent will follow to generate a complete software project from scratch.

Rules for the generated prompt:
1. Be explicit about every file that must be created and its purpose.
2. Include the full technology stack, language version, framework, and directory structure.
3. Specify a CI pipeline Python script (``ci_check.py`` at the project root) that:
   - Validates the project builds successfully (language-appropriate checks).
   - Must NOT be listed in ``.gitignore``.
4. Do not invent requirements beyond what is provided.
5. Be specific and technical — assume the agent is capable but has no prior context.

Reason through the key implementation challenges before writing the prompt."""

    _SYSTEM_FIX = """\
You are an expert prompt engineer for autonomous AI coding agents.
Your task is to produce a targeted **fix prompt** that directs a coding agent to \
correct specific defects identified by a code reviewer.

Rules for the generated fix prompt:
1. The agent has already implemented a full project — do NOT instruct it to start \
from scratch.
2. Include a "DO NOT TOUCH" section listing files/areas that passed review and must \
not be modified.
3. For each defect: state the exact file path, the specific problem, and a concrete \
actionable fix instruction.
4. Include context explaining WHY each fix is needed (import errors, wrong signatures, \
security issues, architecture violations, etc.).
5. If files exceed 200 lines, instruct the agent to split them into smaller modules.
6. If clean architecture violations exist, instruct the agent to refactor per the \
listed issue.
7. Instruct the agent to run the existing ``ci_check.py`` after making all fixes and \
to resolve any CI failures before declaring done.

Reason through each finding (root causes, fix ordering, interdependencies) before \
writing the final fix prompt."""

    # ── Public API ────────────────────────────────────────────────────────────

    def __init__(self, config: AgentOSConfig) -> None:
        self._config = config

    def run(
        self,
        iteration: int,
        requirements_text: Optional[str] = None,
        review_json: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        story_context: Optional[dict] = None,
    ) -> str:
        """Generate a prompt and write it to the configured output path.

        Args:
            iteration: Current pipeline iteration (1 = first generation).
            requirements_text: Raw requirements content for iteration 1. Pass when
                ``iteration == 1``.
            review_json: Review JSON from code reviewer for iteration 2+. Pass when
                ``iteration >= 2``.
            on_stdout: Callback called with each streamed line for real-time display.

        Returns:
            The generated prompt text (also written to disk).
        """
        if iteration == 1:
            if not requirements_text:
                raise ValueError("requirements_text is required for iteration 1")
            prompt_text = self._generate_implementation_prompt(
                requirements_text, iteration, on_stdout, story_context=story_context
            )
        else:
            if not review_json:
                raise ValueError("review_json is required for iteration 2+")
            prompt_text = self._generate_fix_prompt(
                review_json, iteration, on_stdout, story_context=story_context
            )

        self._write_prompt(prompt_text, iteration)
        return prompt_text

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _generate_implementation_prompt(
        self,
        requirements_text: str,
        iteration: int,
        on_stdout: Optional[Callable[[str], None]],
        story_context: Optional[dict] = None,
    ) -> str:
        """Call OpenAI to produce a full implementation prompt from requirements."""
        project_name = self._config.project.name or "the project"
        language = self._config.project.language or "python"

        # Build system prompt — append fork-mode addendum when in GitHub Review mode
        system_prompt = self._SYSTEM_IMPLEMENTATION
        if story_context and story_context.get("is_fork_mode"):
            story_id_str = story_context.get("story_id", "")
            story_title = story_context.get("title", "")
            story_label = f"{story_id_str}: {story_title}" if story_id_str else (story_title or "this story")
            system_prompt = system_prompt + (
                "\n\nIMPORTANT — FORK/STORY MODE:\n"
                f"You are generating a prompt for **{story_label}** on an already-forked repository.\n"
                "The repository has been forked and cloned. Existing code is in place.\n"
                "The generated prompt MUST instruct the coding agent to:\n"
                "  1. Make ONLY the changes required for this story.\n"
                "  2. Do NOT delete, recreate, or rewrite files unrelated to this story.\n"
                "  3. Follow the existing code style, patterns, and directory structure.\n"
                "  4. Run ci_check.py after all changes to validate nothing is broken.\n"
            )

        user_prompt = (
            f"Generate a comprehensive implementation prompt for **{project_name}** "
            f"(language: {language}, iteration {iteration}).\n\n"
            f"Here are the project requirements:\n\n"
            "---\n"
            f"{requirements_text}\n"
            "---\n\n"
            "First reason briefly through the main implementation challenges "
            "(architecture, folder structure, key files, tech stack decisions), "
            "then write the complete implementation prompt."
        )

        fallback = self._fallback_implementation_prompt(
            requirements_text, project_name, language, story_context=story_context
        )
        model = self._config.codex.model_routing.get("PROMPT_GENERATOR", "gpt-4.1-mini")

        return self._stream_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            label=f"iter-{iteration}-impl",
            fallback=fallback,
            on_stdout=on_stdout,
        )

    def _generate_fix_prompt(
        self,
        review_json: str,
        iteration: int,
        on_stdout: Optional[Callable[[str], None]],
        story_context: Optional[dict] = None,
    ) -> str:
        """Call OpenAI to produce a targeted fix prompt from a review JSON."""
        project_name = self._config.project.name or "the project"
        language = self._config.project.language or "python"

        # Build PR/story context note for GitHub Review mode
        pr_note = ""
        if story_context:
            pr_number = story_context.get("pr_number")
            story_id_str = story_context.get("story_id", "")
            story_title = story_context.get("title", "")
            parts: list[str] = []
            if story_id_str or story_title:
                parts.append(f"Story: {story_id_str} — {story_title}".strip(" —"))
            if pr_number:
                parts.append(
                    f"PR #{pr_number} — push fixes to the same branch so the PR is "
                    "updated automatically for re-review."
                )
            if parts:
                pr_note = "\n\n> **CONTEXT**: " + "  \n> ".join(parts)

        user_prompt = (
            f"Generate a fix prompt for **{project_name}** "
            f"(language: {language}, fixing iteration {iteration - 1} → {iteration})."
            + pr_note + "\n\n"
            "Here is the structured review JSON from the code reviewer:\n\n"
            "---\n"
            f"{review_json}\n"
            "---\n\n"
            "Reason through each finding (root cause, fix ordering, interdependencies), "
            "then produce the complete, actionable fix prompt."
        )

        fallback = self._fallback_fix_prompt(
            review_json, project_name, iteration, story_context=story_context
        )
        model = self._config.codex.model_routing.get("PROMPT_GENERATOR", "gpt-4.1-mini")

        return self._stream_llm(
            system_prompt=self._SYSTEM_FIX,
            user_prompt=user_prompt,
            label=f"iter-{iteration}-fix",
            fallback=fallback,
            on_stdout=on_stdout,
        )

    # ── LLM dispatcher ────────────────────────────────────────────────────────

    def _stream_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        label: str,
        fallback: str,
        on_stdout: Optional[Callable[[str], None]],
    ) -> str:
        """Route to Ollama or OpenAI based on prompt_generator.provider config.

        Cascade:
          - provider == "ollama"  → try Ollama first; if it fails try OpenAI; then static fallback
          - provider == "openai"  → try OpenAI first; then static fallback
        """
        pg_cfg = getattr(self._config, "prompt_generator", None)
        provider = getattr(pg_cfg, "provider", "ollama") if pg_cfg else "ollama"

        def _emit(line: str) -> None:
            if on_stdout:
                try:
                    on_stdout(line)
                except Exception:
                    pass

        if provider == "openai":
            model = getattr(pg_cfg, "openai_model", None) or \
                self._config.codex.model_routing.get("PROMPT_GENERATOR", "gpt-4.1-mini")
            return self._stream_openai(system_prompt, user_prompt, model, label, fallback, on_stdout)

        # Ollama path — try Ollama, then cascade to OpenAI on failure
        ollama_cfg = getattr(self._config, "ollama", None)
        model = getattr(pg_cfg, "ollama_model", None) or \
            getattr(ollama_cfg, "model", "llama3.1:8b")
        base_url = getattr(ollama_cfg, "base_url", None) or \
            os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        timeout = getattr(ollama_cfg, "timeout_seconds", 300)

        result = self._stream_ollama(
            system_prompt, user_prompt, model, base_url, timeout,
            label, fallback=None, on_stdout=on_stdout,
        )
        if result is not None:
            return result

        # Ollama failed — try OpenAI as secondary
        openai_model = getattr(pg_cfg, "openai_model", None) or \
            self._config.codex.model_routing.get("PROMPT_GENERATOR", "gpt-4.1-mini")
        api_key = (
            getattr(getattr(self._config, "secrets", None), "openai_api_key", "")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        if api_key:
            _emit(f"[prompt-generator] Ollama unavailable — retrying with OpenAI {openai_model} …")
            result = self._stream_openai(
                system_prompt, user_prompt, openai_model,
                label, fallback=None, on_stdout=on_stdout,
            )
            if result is not None:
                return result

        # Both failed — use static template
        _emit("[prompt-generator] All LLM backends failed — using static fallback template.")
        return fallback

    # ── Ollama streaming ──────────────────────────────────────────────────────

    def _stream_ollama(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        base_url: str,
        timeout: int,
        label: str,
        fallback: Optional[str],
        on_stdout: Optional[Callable[[str], None]],
    ) -> Optional[str]:
        """Call Ollama's OpenAI-compatible /v1/chat/completions with streaming.

        Returns the generated text on success, or ``None`` on any failure.
        """

        def _emit(line: str) -> None:
            if on_stdout:
                try:
                    on_stdout(line)
                except Exception:
                    pass

        # Strip trailing slash; Ollama's OpenAI-compat endpoint lives at /v1
        api_base = base_url.rstrip("/")
        _emit(f"[prompt-generator] Calling Ollama {model} @ {api_base} …")
        logger.info("Generating prompt (%s) via Ollama %s @ %s", label, model, api_base)

        try:
            import openai  # reuse the openai client with a custom base_url

            client = openai.OpenAI(
                api_key="ollama",          # Ollama ignores the key value
                base_url=f"{api_base}/v1",
            )

            resp = client.chat.completions.create(
                model=model,
                stream=True,
                temperature=0.7,
                timeout=float(timeout),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

            full_text: list[str] = []
            for chunk in resp:
                delta = chunk.choices[0].delta.content  # type: ignore[union-attr]
                if not delta:
                    continue
                full_text.append(delta)
                _emit(delta)

            generated = "".join(full_text).strip()
            if generated:
                logger.info("Ollama prompt complete (%s, %d chars)", label, len(generated))
                return generated

            _emit("[prompt-generator] Empty response from Ollama — using fallback.")

        except Exception as exc:
            _emit(f"[prompt-generator] Ollama call failed: {exc} — using fallback.")
            logger.warning("Ollama streaming failed for %s: %s", label, exc)

        return fallback if fallback is not None else None

    # ── OpenAI streaming ──────────────────────────────────────────────────────

    def _stream_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        label: str,
        fallback: Optional[str],
        on_stdout: Optional[Callable[[str], None]],
    ) -> Optional[str]:
        """Call OpenAI with streaming; pipe tokens to on_stdout; return full text.

        Returns the generated text on success, or ``None`` on any failure
        (including missing API key).
        """

        def _emit(line: str) -> None:
            if on_stdout:
                try:
                    on_stdout(line)
                except Exception:
                    pass

        api_key = (
            getattr(self._config, "secrets", None)
            and self._config.secrets.openai_api_key
            or os.environ.get("OPENAI_API_KEY", "")
        )
        if not api_key:
            _emit("[prompt-generator] No OpenAI API key found — skipping OpenAI.")
            logger.warning("No OpenAI API key — skipping OpenAI for %s", label)
            return fallback if fallback is not None else None

        _emit(f"[prompt-generator] Calling {model} …")
        logger.info("Generating prompt (%s) via %s", label, model)

        try:
            import openai

            client = openai.OpenAI(api_key=api_key)

            # Reasoning models don't accept temperature
            _no_temp_prefixes = ("o1", "o3", "o4", "gpt-5")
            create_kwargs: dict = {
                "model": model,
                "stream": True,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if not any(model.startswith(p) for p in _no_temp_prefixes):
                create_kwargs["temperature"] = 0.7

            resp = client.chat.completions.create(**create_kwargs)

            full_text: list[str] = []

            for chunk in resp:
                delta = chunk.choices[0].delta.content  # type: ignore[union-attr]
                if not delta:
                    continue
                full_text.append(delta)
                _emit(delta)  # emit each token chunk for real-time streaming

            generated = "".join(full_text).strip()
            if generated:
                logger.info("Prompt generation complete (%s, %d chars)", label, len(generated))
                return generated

            _emit("[prompt-generator] Empty response from LLM — using fallback.")

        except Exception as exc:
            _emit(f"[prompt-generator] OpenAI call failed: {exc} — using fallback.")
            logger.warning("OpenAI streaming failed for %s: %s", label, exc)

        return fallback if fallback is not None else None

    # ── Disk output ───────────────────────────────────────────────────────────

    def _write_prompt(self, content: str, iteration: int) -> Path:
        """Write the prompt to the fixed configured path (or a default)."""
        prompt_file_path = getattr(self._config.project, "prompt_file_path", "") or ""

        if prompt_file_path:
            out_path = Path(prompt_file_path)
        else:
            # Fallback: data/prompts/latest.md (always overwritten)
            out_path = Path(self._config.storage.db_path).parent / "prompts" / "latest.md"

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")

        # Also keep a per-iteration archive for history
        archive = (
            Path(self._config.storage.db_path).parent
            / "prompts"
            / f"iteration-{iteration}.md"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(content, encoding="utf-8")

        logger.info("Prompt written: %s (%d chars)", out_path, len(content))
        return out_path

    # ── Fallback prompt builders ──────────────────────────────────────────────

    @staticmethod
    def _fallback_implementation_prompt(
        requirements: str, project_name: str, language: str,
        story_context: Optional[dict] = None,
    ) -> str:
        fork_note = ""
        if story_context and story_context.get("is_fork_mode"):
            story_id_str = story_context.get("story_id", "")
            story_title = story_context.get("title", "")
            story_label = (f"{story_id_str} — {story_title}".strip(" —")) or "this story"
            fork_note = (
                f"> **FORK MODE** — Story: {story_label}\n"
                "> You are working on an already-forked repository. "
                "Make ONLY the changes required for this story. "
                "Do NOT recreate or rewrite existing unrelated files.\n\n"
            )
        return (
            f"# Implementation Prompt — {project_name}\n\n"
            f"{fork_note}"
            f"**Language / Stack**: {language}\n\n"
            "## Requirements\n\n"
            f"{requirements}\n\n"
            "## Instructions\n\n"
            "- Implement the complete project based on the requirements above.\n"
            "- Create a clean folder structure appropriate for the stack.\n"
            "- Write a ``ci_check.py`` script at the project root that validates "
            "the build succeeds (syntax check, lint, tests). This file must NOT "
            "be added to ``.gitignore``.\n"
            "- Include all necessary dependency files (requirements.txt, package.json, etc.).\n"
        )

    @staticmethod
    def _fallback_fix_prompt(
        review_json: str, project_name: str, iteration: int,
        story_context: Optional[dict] = None,
    ) -> str:
        pr_note = ""
        if story_context:
            pr_number = story_context.get("pr_number")
            story_id_str = story_context.get("story_id", "")
            story_title = story_context.get("title", "")
            story_label = (f"{story_id_str} — {story_title}".strip(" —")) or ""
            if story_label:
                pr_note += f"> **Story**: {story_label}\n"
            if pr_number:
                pr_note += (
                    f"> **PR #{pr_number}** — push fixes to the same branch so the PR "
                    "is updated automatically for re-review.\n"
                )
            if pr_note:
                pr_note += "\n"
        return (
            f"# Fix Prompt — {project_name} (Iteration {iteration})\n\n"
            f"{pr_note}"
            "You are fixing an **existing implementation**. "
            "Do NOT rewrite the project from scratch.\n\n"
            "## Review Findings\n\n"
            f"```json\n{review_json}\n```\n\n"
            "## Instructions\n\n"
            "- Address every issue listed in the review JSON.\n"
            "- Do not modify files that passed review.\n"
            "- Run ``ci_check.py`` after making fixes and resolve any failures.\n"
        )


