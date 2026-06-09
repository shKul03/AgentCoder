"""Requirement repository — CRUD for the requirements table."""

from __future__ import annotations

import sqlite3

from .models import RequirementRecord


class RequirementRepository:
    """Requirement persistence operations."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, req: RequirementRecord) -> None:
        self._conn.execute(
            """INSERT INTO requirements (id, type, parent_id, title, description, status)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
               type=excluded.type, parent_id=excluded.parent_id,
               title=excluded.title, description=excluded.description,
               status=excluded.status""",
            (req.id, req.type.value, req.parent_id, req.title, req.description, req.status),
        )
        self._conn.commit()

    def get_all(self) -> list[RequirementRecord]:
        rows = self._conn.execute("SELECT * FROM requirements").fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_by_type(self, req_type: str) -> list[RequirementRecord]:
        rows = self._conn.execute(
            "SELECT * FROM requirements WHERE type = ?", (req_type,)
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_children(self, parent_id: str) -> list[RequirementRecord]:
        rows = self._conn.execute(
            "SELECT * FROM requirements WHERE parent_id = ?", (parent_id,)
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row) -> RequirementRecord:
        return RequirementRecord(
            id=row["id"],
            type=row["type"],
            parent_id=row["parent_id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
        )
