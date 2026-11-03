"""Trusted child lineage for a scoped browser session.

Adapted from the DeepSeek Harness session-tree subscription's observed
subagent.started lineage (MIT, ce7073d). Codex IDs are private registry
addresses. Browser cards and lists contain only opaque Host handles.
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from easel.harnesses.events import SUBAGENT_STATUSES, valid_native_subagent_id


def _scope_id(value, maximum=256):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and all(ord(char) >= 32 and ord(char) != 127 for char in value))


class ChatSubagentStore:
    def __init__(self, directory: Path):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "chat_subagents.sqlite3"
        with self._database() as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS edges ("
                "id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, project_id TEXT NOT NULL, "
                "root_session_id TEXT NOT NULL, root_thread_id TEXT NOT NULL, "
                "origin_turn_id TEXT NOT NULL, parent_id TEXT, parent_thread_id TEXT NOT NULL, "
                "child_thread_id TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, "
                "updated_at INTEGER NOT NULL, UNIQUE(actor_id,project_id,child_thread_id))"
            )
            database.execute(
                "CREATE INDEX IF NOT EXISTS edges_root "
                "ON edges(actor_id,project_id,root_thread_id,root_session_id,created_at)"
            )

    @contextmanager
    def _database(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as database, database:
            database.row_factory = sqlite3.Row
            yield database

    @staticmethod
    def _public(row):
        return {
            "id": row["id"], "parentId": row["parent_id"], "title": "子 Agent",
            "rootSessionId": row["root_session_id"], "originTurnId": row["origin_turn_id"],
            "status": row["status"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def record(self, *, actor_id, project_id, session_id, turn_id, update) -> dict | None:
        """Insert a confirmed spawn or update a known edge without changing lineage."""
        if not all(_scope_id(value) for value in (actor_id, project_id, session_id, turn_id)):
            return None
        if not isinstance(update, dict) or set(update) != {
                "rootThreadId", "parentThreadId", "childThreadId", "action", "status"}:
            return None
        root, sender, child = (update[key] for key in ("rootThreadId", "parentThreadId", "childThreadId"))
        action, status = update["action"], update["status"]
        if (not all(valid_native_subagent_id(value) for value in (root, sender, child))
                or child in (root, sender) or action not in ("spawn", "update")
                or not isinstance(status, str) or status not in SUBAGENT_STATUSES):
            return None
        identity = (actor_id, project_id)
        scope = (*identity, root, session_id)
        with self._database() as database:
            database.execute("BEGIN IMMEDIATE")
            parent_id = None
            if sender != root:
                parent = database.execute(
                    "SELECT id FROM edges WHERE actor_id=? AND project_id=? AND root_thread_id=? "
                    "AND root_session_id=? AND child_thread_id=?", (*scope, sender)).fetchone()
                if parent is None:
                    return None
                parent_id = parent["id"]
            previous = database.execute(
                "SELECT * FROM edges WHERE actor_id=? AND project_id=? AND child_thread_id=?",
                (*identity, child)).fetchone()
            now = int(time.time() * 1000)
            if action == "update":
                if (previous is None or previous["root_thread_id"] != root
                        or previous["root_session_id"] != session_id or previous["status"] == status):
                    return None
                handle = previous["id"]
                database.execute("UPDATE edges SET status=?,updated_at=? WHERE id=?",
                                 (status, max(now, previous["updated_at"] + 1), handle))
            else:
                # Replayed spawn observations never change an existing edge,
                # including a previously recorded terminal or resumed status.
                if previous is not None:
                    return None
                conflict = database.execute(
                    "SELECT 1 FROM edges WHERE actor_id=? AND project_id=? AND ("
                    "(root_thread_id=? AND root_session_id<>?) OR root_thread_id=? OR child_thread_id=?) LIMIT 1",
                    (*scope, child, root)).fetchone()
                if conflict is not None:
                    return None
                handle = uuid.uuid4().hex
                database.execute(
                    "INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (handle, *identity, session_id, root, turn_id, parent_id, sender, child, status, now, now))
            row = database.execute("SELECT * FROM edges WHERE id=?", (handle,)).fetchone()
            return self._public(row)

    def list(self, *, actor_id, project_id, session_id, root_thread_id) -> list[dict]:
        with self._database() as database:
            rows = database.execute(
                "SELECT * FROM edges WHERE actor_id=? AND project_id=? AND root_session_id=? AND root_thread_id=? "
                "ORDER BY created_at, rowid", (actor_id, project_id, session_id, root_thread_id))
            return [self._public(row) for row in rows]

    def resolve(self, *, actor_id, project_id, session_id, root_thread_id, handle) -> dict | None:
        """Resolve a Host handle for an authorized native read. Never serialize directly."""
        if not isinstance(handle, str) or not re.fullmatch(r"[a-f0-9]{32}", handle):
            return None
        with self._database() as database:
            row = database.execute(
                "SELECT * FROM edges WHERE actor_id=? AND project_id=? AND root_session_id=? AND root_thread_id=? "
                "AND id=?", (actor_id, project_id, session_id, root_thread_id, handle)).fetchone()
            if row is None:
                return None
            return {**self._public(row), "rootThreadId": row["root_thread_id"],
                    "parentThreadId": row["parent_thread_id"], "childThreadId": row["child_thread_id"]}
