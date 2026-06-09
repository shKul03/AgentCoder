"""Pipeline state repository — CRUD for the singleton pipeline_state row."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .models import PipelineState, PipelineStatus


class PipelineRepository:
    """Pipeline state persistence (singleton row with id=1)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # Keys used to persist story-context fields inside metadata JSON
    _STORY_KEYS = ("current_story_id", "stories_completed", "stories_total")

    def get_state(self) -> PipelineState:
        row = self._conn.execute("SELECT * FROM pipeline_state WHERE id = 1").fetchone()
        try:
            status = PipelineStatus(row["pipeline_status"])
        except ValueError:
            # Stale status value from an old schema — fall back to IDLE
            status = PipelineStatus.IDLE
        meta = json.loads(row["metadata"])
        return PipelineState(
            current_iteration=row["current_iteration"],
            pipeline_status=status,
            last_checkpoint=datetime.fromisoformat(row["last_checkpoint"]),
            metadata={k: v for k, v in meta.items() if k not in self._STORY_KEYS},
            current_story_id=meta.get("current_story_id"),
            stories_completed=meta.get("stories_completed", 0),
            stories_total=meta.get("stories_total", 0),
        )

    def save_state(self, state: PipelineState) -> None:
        # Persist story-context fields inside metadata so the DB schema is unchanged.
        meta = dict(state.metadata)
        if state.current_story_id is not None:
            meta["current_story_id"] = state.current_story_id
        meta["stories_completed"] = state.stories_completed
        meta["stories_total"] = state.stories_total
        self._conn.execute(
            """UPDATE pipeline_state SET
               current_iteration = ?, pipeline_status = ?,
               last_checkpoint = ?, metadata = ? WHERE id = 1""",
            (
                state.current_iteration,
                state.pipeline_status.value,
                datetime.utcnow().isoformat(),
                json.dumps(meta),
            ),
        )
        self._conn.commit()
