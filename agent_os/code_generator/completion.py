"""Completion detection for Code Generator output.

Completion is determined solely by the process exit code:
  exit 0         → COMPLETE
  exit non-zero  → FAILED
  timed out      → FAILED
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class CompletionStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class CompletionResult:
    """Result of completion detection."""

    __slots__ = ("status", "summary_text", "reason")

    def __init__(
        self, status: CompletionStatus, summary_text: str = "", reason: str = ""
    ) -> None:
        self.status = status
        self.summary_text = summary_text
        self.reason = reason


def detect_completion(
    exit_code: int,
    working_dir: str | Path,
    timed_out: bool = False,
) -> CompletionResult:
    """Determine completion status from the process exit code."""
    if timed_out:
        return CompletionResult(
            CompletionStatus.FAILED, reason="Codex CLI timed out."
        )

    if exit_code != 0:
        return CompletionResult(
            CompletionStatus.FAILED,
            reason=f"Codex CLI exited with code {exit_code}.",
        )

    logger.info("Completion detected: process exited 0.")
    return CompletionResult(CompletionStatus.COMPLETE)


def consume_summary(working_dir: str | Path) -> str:
    """No-op — summary.md is no longer used by the pipeline."""
    return ""

