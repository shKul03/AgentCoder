"""Story Queue Manager — builds and manages the ordered story processing queue.

The queue is used exclusively in GitHub Review mode. Stories are ordered using
LLM-powered dependency analysis so that foundational stories (auth, data models,
core services) are processed before stories that build on them.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from typing import Any, Optional

from ..storage.database import Database
from ..storage.models import StoryQueueItem, StoryStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM dependency analysis
# ---------------------------------------------------------------------------

_ANALYSIS_SYSTEM_PROMPT = """\
You are a software engineering assistant. Given a list of user stories, analyse which \
stories have implementation dependencies on other stories (i.e. Story B cannot be \
implemented before Story A because it relies on code/APIs/models introduced in A).

Return a JSON array where each object has:
  - "story_id": string — the story's ID
  - "depends_on": array of story_id strings that MUST be completed first
  - "reason": string — brief (≤20 words) explanation of the dependency, or "" if none

Only include logical code-level dependencies, not business priority. Avoid circular \
dependencies — if you detect a cycle, break it at the weakest link.
"""


def _build_analysis_prompt(stories: list[dict[str, Any]]) -> str:
    lines = []
    for s in stories:
        lines.append(f"story_id: {s['story_id']}")
        lines.append(f"  title: {s['title']}")
        if s.get("description"):
            lines.append(f"  description: {s['description'][:300]}")
        if s.get("acceptance_criteria"):
            ac = s["acceptance_criteria"]
            if isinstance(ac, list):
                ac = "; ".join(ac[:3])
            lines.append(f"  acceptance_criteria: {ac[:200]}")
        lines.append("")
    return "Stories:\n" + "\n".join(lines)


async def analyse_dependencies(
    stories: list[dict[str, Any]],
    *,
    api_key: str = "",
    model: str = "gpt-4o-mini",
) -> list[dict[str, Any]]:
    """Call the configured LLM to determine story ordering.

    Falls back to identity order (no dependencies) if the LLM is unavailable
    or returns malformed JSON — so the pipeline never hard-fails here.

    Args:
        stories: list of dicts with at least ``story_id``, ``title``.
        api_key: OpenAI API key (passed from the running orchestrator config).
        model:   OpenAI model name to use for dependency analysis.

    Returns:
        Same list with ``depends_on`` (list[str]) and ``dependency_reason`` (str)
        fields merged in.
    """
    try:
        from openai import AsyncOpenAI  # lazy import

        if not api_key:
            logger.warning("No OpenAI API key — skipping dependency analysis, using story order as-is")
            return _no_op_dependencies(stories)

        client = AsyncOpenAI(api_key=api_key)  # type: ignore[arg-type]
        user_prompt = _build_analysis_prompt(stories)

        try:
            response = await client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=2000,
            )
        except Exception:
            await client.close()
            raise
        await client.close()

        raw = response.choices[0].message.content or "{}"
        # The model wraps the array in an object; accept both forms.
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            # look for any array value
            for v in parsed.values():
                if isinstance(v, list):
                    parsed = v
                    break
            else:
                parsed = []

        dep_map: dict[str, dict] = {}
        for entry in parsed:
            sid = entry.get("story_id", "")
            if sid:
                dep_map[sid] = {
                    "depends_on": entry.get("depends_on", []),
                    "dependency_reason": entry.get("reason", ""),
                }

        result = []
        for story in stories:
            sid = story["story_id"]
            merged = {**story, **dep_map.get(sid, {"depends_on": [], "dependency_reason": ""})}
            result.append(merged)

        return result

    except Exception as exc:
        err_str = str(exc)
        if "401" in err_str or "invalid_api_key" in err_str or "AuthenticationError" in type(exc).__name__:
            logger.warning(
                "Dependency analysis skipped — OpenAI API key invalid or missing. "
                "Stories will be processed in natural order."
            )
        else:
            logger.exception("Dependency analysis failed — falling back to natural order")
        return _no_op_dependencies(stories)


def _no_op_dependencies(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**s, "depends_on": [], "dependency_reason": ""} for s in stories]


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------

def topological_sort(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kahn's algorithm topological sort by ``depends_on``.

    Preserves original order among stories with equal dependency depth.
    Stories with unknown depends_on IDs are treated as having no dependency on
    those unknowns (soft-ignore) to be resilient to LLM hallucinations.
    """
    id_to_story = {s["story_id"]: s for s in stories}
    valid_ids = set(id_to_story)

    in_degree: dict[str, int] = {s["story_id"]: 0 for s in stories}
    dependents: dict[str, list[str]] = {s["story_id"]: [] for s in stories}

    for story in stories:
        for dep in story.get("depends_on", []):
            if dep in valid_ids and dep != story["story_id"]:
                in_degree[story["story_id"]] += 1
                dependents[dep].append(story["story_id"])

    # Queue: stories with no unresolved dependencies (stable order)
    queue: list[str] = [s["story_id"] for s in stories if in_degree[s["story_id"]] == 0]
    ordered: list[dict[str, Any]] = []

    while queue:
        sid = queue.pop(0)
        ordered.append(id_to_story[sid])
        for child in dependents[sid]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Append any remaining (cycle remnants) at the end in original order
    ordered_ids = {s["story_id"] for s in ordered}
    for story in stories:
        if story["story_id"] not in ordered_ids:
            logger.warning("Story %s is part of a dependency cycle — appending at end", story["story_id"])
            ordered.append(story)

    return ordered


# ---------------------------------------------------------------------------
# StoryQueueManager
# ---------------------------------------------------------------------------

class StoryQueueManager:
    """Manages the story_queue table for a single pipeline run.

    Usage (GitHub Review mode engine)::

        manager = StoryQueueManager(db)
        await manager.build_queue(raw_stories)  # populates DB
        item = manager.dequeue()                 # get next QUEUED item
        manager.mark_complete(item.story_id)
        manager.mark_failed(item.story_id, reason="timeout")
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Queue construction
    # ------------------------------------------------------------------

    async def build_queue(
        self,
        raw_stories: list[dict[str, Any]],
        *,
        api_key: str = "",
        model: str = "gpt-4o-mini",
    ) -> list[StoryQueueItem]:
        """Analyse dependencies, sort stories, persist to DB, and return items.

        Clears any existing queue entries before writing new ones.

        Args:
            raw_stories: list of dicts with at least ``story_id``, ``title``.
                         Optional keys: ``description``, ``acceptance_criteria``.
            api_key: OpenAI API key for dependency analysis (from orchestrator config).
            model:   OpenAI model name to use for dependency analysis.

        Returns:
            Ordered list of :class:`StoryQueueItem` objects (position 0 first).
        """
        enriched = await analyse_dependencies(raw_stories, api_key=api_key, model=model)
        ordered = topological_sort(enriched)

        now = datetime.utcnow().isoformat()
        conn = self._db.conn

        conn.execute("DELETE FROM story_queue")
        items: list[StoryQueueItem] = []
        for pos, story in enumerate(ordered):
            ac = story.get("acceptance_criteria", [])
            if isinstance(ac, str):
                ac = [ac]
            conn.execute(
                """
                INSERT INTO story_queue
                    (story_id, title, description, acceptance_criteria,
                     position, status, branch_name, pr_number, pr_url,
                     story_iteration, depends_on, dependency_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    story["story_id"],
                    story.get("title", ""),
                    story.get("description", ""),
                    json.dumps(ac),
                    pos,
                    StoryStatus.QUEUED.value,
                    "",        # branch_name — set later
                    None,      # pr_number
                    "",        # pr_url
                    0,         # story_iteration
                    json.dumps(story.get("depends_on", [])),
                    story.get("dependency_reason", ""),
                    now,
                ),
            )
            items.append(
                StoryQueueItem(
                    story_id=story["story_id"],
                    title=story.get("title", ""),
                    description=story.get("description", ""),
                    acceptance_criteria=ac,
                    position=pos,
                    depends_on=story.get("depends_on", []),
                    dependency_reason=story.get("dependency_reason", ""),
                    created_at=datetime.utcnow(),
                )
            )
        return items

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    def dequeue(self) -> Optional[StoryQueueItem]:
        """Return the next QUEUED story (lowest position) and mark it IN_PROGRESS.

        Returns None if the queue is empty or all stories are done/failed.
        Respects dependency ordering — a story is only dequeued if all stories
        it depends_on have status COMPLETED.
        """
        conn = self._db.conn
        rows = conn.execute(
            "SELECT * FROM story_queue WHERE status = ? ORDER BY position",
            (StoryStatus.QUEUED.value,),
        ).fetchall()

        completed_ids = {
            r["story_id"]
            for r in conn.execute(
                "SELECT story_id FROM story_queue WHERE status = ?",
                (StoryStatus.COMPLETED.value,),
            ).fetchall()
        }

        for row in rows:
            deps = json.loads(row["depends_on"] or "[]")
            if all(d in completed_ids for d in deps):
                conn.execute(
                    "UPDATE story_queue SET status = ? WHERE story_id = ?",
                    (StoryStatus.IN_PROGRESS.value, row["story_id"]),
                )
                return self._row_to_item(row)

        return None

    def peek(self) -> Optional[StoryQueueItem]:
        """Return the next ready story without changing its status."""
        conn = self._db.conn
        completed_ids = {
            r["story_id"]
            for r in conn.execute(
                "SELECT story_id FROM story_queue WHERE status = ?",
                (StoryStatus.COMPLETED.value,),
            ).fetchall()
        }
        rows = conn.execute(
            "SELECT * FROM story_queue WHERE status = ? ORDER BY position",
            (StoryStatus.QUEUED.value,),
        ).fetchall()
        for row in rows:
            deps = json.loads(row["depends_on"] or "[]")
            if all(d in completed_ids for d in deps):
                return self._row_to_item(row)
        return None

    def mark_complete(self, story_id: str, pr_number: Optional[int] = None, pr_url: str = "") -> None:
        conn = self._db.conn
        conn.execute(
            """UPDATE story_queue
               SET status = ?, completed_at = ?, pr_number = ?, pr_url = ?
               WHERE story_id = ?""",
            (
                StoryStatus.COMPLETED.value,
                datetime.utcnow().isoformat(),
                pr_number,
                pr_url,
                story_id,
            ),
        )

    def mark_failed(self, story_id: str, reason: str = "") -> None:
        conn = self._db.conn
        conn.execute(
            "UPDATE story_queue SET status = ?, dependency_reason = ? WHERE story_id = ?",
            (StoryStatus.FAILED.value, reason, story_id),
        )

    def increment_iteration(self, story_id: str) -> int:
        """Bump the per-story iteration counter and return the new value."""
        conn = self._db.conn
        conn.execute(
            "UPDATE story_queue SET story_iteration = story_iteration + 1 WHERE story_id = ?",
            (story_id,),
        )
        row = conn.execute(
            "SELECT story_iteration FROM story_queue WHERE story_id = ?",
            (story_id,),
        ).fetchone()
        return row["story_iteration"] if row else 0

    def update_branch(self, story_id: str, branch_name: str) -> None:
        conn = self._db.conn
        conn.execute(
            "UPDATE story_queue SET branch_name = ? WHERE story_id = ?",
            (branch_name, story_id),
        )

    # ------------------------------------------------------------------
    # Read / introspection
    # ------------------------------------------------------------------

    def get_item(self, story_id: str) -> Optional[StoryQueueItem]:
        conn = self._db.conn
        row = conn.execute(
            "SELECT * FROM story_queue WHERE story_id = ?", (story_id,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def get_queue_state(self) -> list[dict[str, Any]]:
        """Return full queue as a list of serialisable dicts — for API/UI consumption."""
        conn = self._db.conn
        rows = conn.execute(
            "SELECT * FROM story_queue ORDER BY position"
        ).fetchall()
        return [dict(r) for r in rows]

    def is_complete(self) -> bool:
        """True when every story is COMPLETED or FAILED (none left in QUEUED/IN_PROGRESS)."""
        conn = self._db.conn
        row = conn.execute(
            "SELECT COUNT(*) as n FROM story_queue WHERE status IN (?, ?)",
            (StoryStatus.QUEUED.value, StoryStatus.IN_PROGRESS.value),
        ).fetchone()
        return (row["n"] if row else 0) == 0

    def counts(self) -> dict[str, int]:
        """Return {queued, in_progress, completed, failed, total} counts."""
        conn = self._db.conn
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM story_queue GROUP BY status"
        ).fetchall()
        counts: dict[str, int] = {"queued": 0, "in_progress": 0, "completed": 0, "failed": 0}
        total = 0
        for row in rows:
            counts[row["status"]] = row["n"]
            total += row["n"]
        counts["total"] = total
        return counts

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> StoryQueueItem:
        return StoryQueueItem(
            id=row["id"],
            story_id=row["story_id"],
            title=row["title"],
            description=row["description"] or "",
            acceptance_criteria=json.loads(row["acceptance_criteria"] or "[]"),
            position=row["position"],
            status=StoryStatus(row["status"]),
            branch_name=row["branch_name"] or "",
            pr_number=row["pr_number"],
            pr_url=row["pr_url"] or "",
            story_iteration=row["story_iteration"] or 0,
            depends_on=json.loads(row["depends_on"] or "[]"),
            dependency_reason=row["dependency_reason"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        )
