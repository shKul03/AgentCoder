"""Agent config & file persistence repository.

Provides two concerns:
1. agent_config  — key-value store for `model_routing`, `model_routing_defaults`,
                   and `registry` (all stored as JSON strings).
2. agent_files   — mirrors .md file content for every agent so edits made
                   through the UI are recoverable even if the files on disk
                   are lost; also acts as the single source-of-truth for
                   future DB-first reads.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


class AgentConfigRepo:
    """CRUD for agent_config and agent_files tables."""

    def __init__(self, conn) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # agent_config — generic key / JSON-value store
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Return the parsed JSON value for *key*, or None if absent."""
        row = self._conn.execute(
            "SELECT value FROM agent_config WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def set(self, key: str, value: Any) -> None:
        """Upsert a key with *value* (serialised to JSON)."""
        self._conn.execute(
            """
            INSERT INTO agent_config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE
                SET value = excluded.value,
                    updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), datetime.utcnow().isoformat()),
        )
        self._conn.commit()

    def get_model_routing(self) -> dict[str, str] | None:
        return self.get("model_routing")

    def set_model_routing(self, routing: dict[str, str]) -> None:
        self.set("model_routing", routing)
        # Bootstrap defaults only once (never overwrite after first save)
        if self.get("model_routing_defaults") is None:
            self.set("model_routing_defaults", routing)

    def get_model_routing_defaults(self) -> dict[str, str] | None:
        return self.get("model_routing_defaults")

    def set_registry(self, registry: dict[str, str]) -> None:
        self.set("registry", registry)

    def get_registry(self) -> dict[str, str] | None:
        return self.get("registry")

    # ------------------------------------------------------------------
    # Secrets — stored encrypted-at-rest is out of scope here; the DB
    # is local-only so storing plaintext is acceptable for a local tool.
    # Secrets are stored under key "secrets" as a JSON dict.
    # ------------------------------------------------------------------

    def get_secrets(self) -> dict[str, str]:
        """Return stored secrets dict (may be empty)."""
        return self.get("secrets") or {}

    def set_secrets(self, openai_api_key: str = "", github_token: str = "") -> None:
        """Upsert secrets into DB; only overwrites non-empty values."""
        current = self.get_secrets()
        if openai_api_key:
            current["openai_api_key"] = openai_api_key
        if github_token:
            current["github_token"] = github_token
        self.set("secrets", current)

    # ------------------------------------------------------------------
    # agent_files — per-agent .md content mirror
    # ------------------------------------------------------------------

    def upsert_file(self, agent_name: str, file_name: str, content: str) -> None:
        """Insert or replace the content of an agent's .md file in the DB."""
        self._conn.execute(
            """
            INSERT INTO agent_files (agent_name, file_name, content, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_name, file_name) DO UPDATE
                SET content = excluded.content,
                    updated_at = excluded.updated_at
            """,
            (agent_name, file_name, content, datetime.utcnow().isoformat()),
        )
        self._conn.commit()

    def get_file(self, agent_name: str, file_name: str) -> str | None:
        """Return the stored content for a file, or None if not yet persisted."""
        row = self._conn.execute(
            "SELECT content FROM agent_files WHERE agent_name = ? AND file_name = ?",
            (agent_name, file_name),
        ).fetchone()
        return row["content"] if row else None

    def list_agent_files(self, agent_name: str) -> dict[str, str]:
        """Return all stored files for an agent as {file_name: content}."""
        rows = self._conn.execute(
            "SELECT file_name, content FROM agent_files WHERE agent_name = ?",
            (agent_name,),
        ).fetchall()
        return {row["file_name"]: row["content"] for row in rows}

    def delete_agent_files(self, agent_name: str) -> None:
        """Remove all stored .md file entries for an agent from the DB."""
        self._conn.execute(
            "DELETE FROM agent_files WHERE agent_name = ?", (agent_name,)
        )
        self._conn.commit()
