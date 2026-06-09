"""Iteration repository — CRUD for the iterations table."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from .models import IterationRecord, IterationStatus


class IterationRepository:
    """Iteration persistence operations."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, iteration: IterationRecord) -> int:
        cursor = self._conn.execute(
            """INSERT INTO iterations (module_id, iteration_number, status,
               prompt_path, prompt_content, review_json_path, review_content,
               summary_path, token_usage, started_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(module_id, iteration_number) DO UPDATE SET
               status = excluded.status,
               prompt_path = excluded.prompt_path,
               prompt_content = excluded.prompt_content,
               review_json_path = excluded.review_json_path,
               review_content = excluded.review_content,
               summary_path = excluded.summary_path,
               token_usage = excluded.token_usage,
               started_at = excluded.started_at,
               completed_at = excluded.completed_at""",
            (
                iteration.module_id,
                iteration.iteration_number,
                iteration.status.value,
                iteration.prompt_path,
                iteration.prompt_content,
                iteration.review_json_path,
                iteration.review_content,
                iteration.summary_path,
                iteration.token_usage,
                iteration.started_at.isoformat(),
                iteration.completed_at.isoformat() if iteration.completed_at else None,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def update(self, iteration: IterationRecord) -> None:
        self._conn.execute(
            """UPDATE iterations SET status = ?, prompt_path = ?, prompt_content = ?,
               review_json_path = ?, review_content = ?, summary_path = ?,
               token_usage = ?, completed_at = ?
               WHERE module_id = ? AND iteration_number = ?""",
            (
                iteration.status.value,
                iteration.prompt_path,
                iteration.prompt_content,
                iteration.review_json_path,
                iteration.review_content,
                iteration.summary_path,
                iteration.token_usage,
                iteration.completed_at.isoformat() if iteration.completed_at else None,
                iteration.module_id,
                iteration.iteration_number,
            ),
        )
        self._conn.commit()

    def get(self, module_id: str, iteration_number: int) -> Optional[IterationRecord]:
        row = self._conn.execute(
            "SELECT * FROM iterations WHERE module_id = ? AND iteration_number = ?",
            (module_id, iteration_number),
        ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def get_for_module(self, module_id: str) -> list[IterationRecord]:
        rows = self._conn.execute(
            "SELECT * FROM iterations WHERE module_id = ? ORDER BY iteration_number",
            (module_id,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row) -> IterationRecord:
        return IterationRecord(
            id=row["id"],
            module_id=row["module_id"],
            iteration_number=row["iteration_number"],
            status=IterationStatus(row["status"]),
            prompt_path=row["prompt_path"],
            prompt_content=row["prompt_content"] if "prompt_content" in row.keys() else "",
            review_json_path=row["review_json_path"],
            review_content=row["review_content"] if "review_content" in row.keys() else "",
            summary_path=row["summary_path"],
            token_usage=row["token_usage"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
        )
