"""Code reviewer structured output schemas — Phase 3.3.

The primary schema is ``ReviewJSON``, which is the structured output written by
the code reviewer after analysing a PR diff.  It is read by the orchestrator and
fed into the prompt generator to produce a targeted fix prompt for the next
iteration.

Legacy fields (``CodeReviewResult``, ``FileReviewResult``, etc.) are retained as
a migration alias pointing at *ReviewJSON* to avoid hard-baking any code outside
this package to the old layout.  They will be removed in a future cleanup phase.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Common enums ─────────────────────────────────────────────────────────────

class IssueSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IssueCategory(str, Enum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DESIGN = "design"
    STYLE = "style"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    ARCHITECTURE = "architecture"
    FOLDER_STRUCTURE = "folder_structure"
    FILE_SIZE = "file_size"
    OTHER = "other"


# ── Phase 3.3 primary schema ──────────────────────────────────────────────────

class LineComment(BaseModel):
    """An inline review comment attached to a specific file and line."""
    model_config = ConfigDict(extra="allow")

    file: str
    line: int = 0
    comment: str
    severity: IssueSeverity = IssueSeverity.MEDIUM
    checklist_item: str = ""
    suggested_fix: str = ""


class GlobalComment(BaseModel):
    """A PR-level comment not attached to a specific file line."""
    model_config = ConfigDict(extra="allow")

    comment: str
    category: str = "general"   # "architecture" | "folder_structure" | "security" | "general"
    severity: IssueSeverity = IssueSeverity.MEDIUM


class FolderStructureIssue(BaseModel):
    """A file that is placed in the wrong directory."""
    model_config = ConfigDict(extra="allow")

    path: str
    issue: str
    expected_location: str = ""


class ArchitectureIssue(BaseModel):
    """A clean-architecture violation."""
    model_config = ConfigDict(extra="allow")

    description: str
    layer: str = ""           # e.g. "route", "service", "repository", "ui"
    severity: IssueSeverity = IssueSeverity.HIGH


class FileSizeViolation(BaseModel):
    """A file that exceeds the allowed 200-line limit."""
    model_config = ConfigDict(extra="allow")

    file: str
    line_count: int


class ReviewJSON(BaseModel):
    """Structured review output produced by the code reviewer for a single PR.

    Checklist keys (15 items, each scored 0-100):
        code_correctness, readability, structure_design, performance,
        security, error_handling, code_standards, testing,
        documentation, maintainability, dependencies, logging,
        version_control, ui_ux, overall_impact
    """
    model_config = ConfigDict(extra="allow")

    overall_status: str = "needs_work"     # "needs_work" | "accepted" | "rejected"
    checklist_scores: dict[str, int] = Field(default_factory=dict)
    overall_score: int = Field(default=0, ge=0, le=100)
    line_comments: list[LineComment] = Field(default_factory=list)
    global_comments: list[GlobalComment] = Field(default_factory=list)
    folder_structure_issues: list[FolderStructureIssue] = Field(default_factory=list)
    architecture_issues: list[ArchitectureIssue] = Field(default_factory=list)
    file_size_violations: list[FileSizeViolation] = Field(default_factory=list)
    summary: str = ""
    # ── GitHub Review mode fields (Phase 4) ──────────────────────────────────
    pr_number: Optional[int] = None       # which PR was reviewed
    pr_url: str = ""                      # full PR HTML URL
    story_id: str = ""                    # which story this review belongs to

    @property
    def has_blocking_issues(self) -> bool:
        """Return True if any critical/high severity issues exist."""
        for c in self.line_comments:
            if c.severity in (IssueSeverity.CRITICAL, IssueSeverity.HIGH):
                return True
        for c in self.global_comments:
            if c.severity in (IssueSeverity.CRITICAL, IssueSeverity.HIGH):
                return True
        for a in self.architecture_issues:
            if a.severity in (IssueSeverity.CRITICAL, IssueSeverity.HIGH):
                return True
        return False

    def compute_overall_score(self) -> int:
        """Return weighted average of checklist_scores, storing result in overall_score."""
        if not self.checklist_scores:
            return self.overall_score
        total = sum(self.checklist_scores.values())
        self.overall_score = total // len(self.checklist_scores)
        return self.overall_score


# ── ReviewRunResult — wraps ReviewJSON with raw text + metadata ───────────────

class ReviewRunResult:
    """Result of a CodeReviewerRunner.run() call."""

    __slots__ = ("review", "raw_text", "review_json_path", "comments_posted",
                 "pr_merged", "branch_deleted")

    def __init__(
        self,
        review: ReviewJSON,
        raw_text: str = "",
        review_json_path: str = "",
        comments_posted: int = 0,
        pr_merged: bool = False,
        branch_deleted: bool = False,
    ) -> None:
        self.review = review
        self.raw_text = raw_text
        self.review_json_path = review_json_path
        self.comments_posted = comments_posted
        self.pr_merged = pr_merged
        self.branch_deleted = branch_deleted


# ── Backward-compat aliases (kept to avoid breakage in external code) ─────────

class ReviewIssue(BaseModel):
    """Backward-compat issue wrapper — maps to LineComment fields."""
    model_config = ConfigDict(extra="allow")

    id: str = ""
    file: str = ""
    line_start: int = 0
    line_end: int = 0
    severity: IssueSeverity = IssueSeverity.MEDIUM
    category: IssueCategory = IssueCategory.OTHER
    issue: str = ""
    suggested_fix: str = ""


class FileAction(str, Enum):
    ACCEPT = "accept"
    PATCH = "patch"
    REGENERATE = "regenerate"


class FileReviewResult(BaseModel):
    """Backward-compat per-file review (pre-Phase-3.3)."""
    model_config = ConfigDict(extra="allow")

    file_path: str
    action: FileAction = FileAction.ACCEPT
    issues: list[ReviewIssue] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)


class CodeReviewResult(BaseModel):
    """Backward-compat top-level review (pre-Phase-3.3).

    Retained so that any existing serialised JSON can still be parsed.
    New code should use ReviewJSON.
    """
    model_config = ConfigDict(extra="allow")

    module_id: str = ""
    iteration: int = 0
    overall_status: str = "needs_work"
    convergence_score: int = Field(default=0, ge=0, le=100)
    files: list[FileReviewResult] = Field(default_factory=list)
    area_scores: list[dict] = Field(default_factory=list)
    summary: str = ""
    blocking_issues: int = 0
    checklist_scores: dict[str, int] = Field(default_factory=dict)
    syntax_errors_found: list[str] = Field(default_factory=list)
    test_failures_acknowledged: bool = False

    def compute_summary_fields(self) -> None:
        """Recompute blocking_issues count from issues."""
        from .schema import IssueSeverity as _S
        self.blocking_issues = sum(
            1
            for f in self.files
            for i in f.issues
            if i.severity in (_S.CRITICAL, _S.HIGH)
        )

    def to_review_json(self) -> ReviewJSON:
        """Convert legacy CodeReviewResult to the new ReviewJSON schema."""
        line_comments = []
        for f in self.files:
            for issue in f.issues:
                line_comments.append(LineComment(
                    file=f.file_path,
                    line=issue.line_start,
                    comment=issue.issue,
                    severity=issue.severity,
                    checklist_item=issue.category.value,
                    suggested_fix=issue.suggested_fix,
                ))
        return ReviewJSON(
            overall_status=self.overall_status,
            checklist_scores=self.checklist_scores,
            overall_score=self.convergence_score,
            line_comments=line_comments,
            summary=self.summary,
        )

