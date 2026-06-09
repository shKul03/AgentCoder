"""Pydantic models for prompt generator inputs and outputs.

FileVerdict and FileReview have been removed — those concepts now live in the
code reviewer's ReviewJSON schema (Phase 3.3). This module only describes the
inputs that PromptGeneratorRunner accepts.
"""

from __future__ import annotations

from pydantic import BaseModel


class PromptGeneratorInput(BaseModel):
    """Input descriptor passed to the prompt generator runner."""

    iteration: int = 1
    requirements_text: str = ""   # populated for iteration 1
    review_json: str = ""          # populated for iteration 2+


class PromptGeneratorResult(BaseModel):
    """Result returned by PromptGeneratorRunner.run()."""

    iteration: int
    prompt_text: str
    prompt_file_path: str
    model_used: str = ""
    char_count: int = 0

