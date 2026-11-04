"""Durable browser-session catalog scoped by the caller's verified directory.

This is the Easel SQLite storage adapter for the Host-owned session-directory
pattern in DeepSeek Harness session-controller (MIT, ce7073d). No native engine
transcript is removed or rewritten by catalog operations.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from pathlib import Path

from easel.harnesses.models import valid_model_id
from web.chat_history_window import (
    HISTORY_WINDOW_TURNS,
    ChatHistoryWindowConflict,
    merge_history_window,
    older_history_window,
    project_history_window,
    validate_history_window,
)

class ChatSessionDeletedError(ValueError):
    """The current scoped catalog retired this session before admission."""


def validated_session(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("无效的会话")
    identifier = value.get("id")
    messages = value.get("messages")
    if (not isinstance(identifier, str) or not identifier or len(identifier) > 256
            or any(ord(char) < 32 for char in identifier) or not isinstance(messages, list)):
        raise ValueError("无效的会话标识或消息")
    for message in messages:
        if (not isinstance(message, dict) or message.get("role") not in ("user", "assistant")
                or not isinstance(message.get("content"), str)):
            raise ValueError("无效的消息")
    updated = value.get("updated", value.get("updatedAt", value.get("created", 0)))
    if isinstance(updated, bool) or not isinstance(updated, (int, float)) or not math.isfinite(updated) or updated < 0:
        raise ValueError("无效的会话更新时间")
    title = value.get("title", "新对话")
    if not isinstance(title, str) or len(title) > 1000:
        raise ValueError("无效的会话标题")
    if value.get("persona") is not None and not isinstance(value["persona"], str):
        raise ValueError("无效的画像")
    queued = value.get("queued", [])
    if not isinstance(queued, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
        or not isinstance(item.get("text"), str) or not isinstance(item.get("attachments", []), list)
        or ("delivery" in item and item["delivery"] not in ("sending", "rejected", "unconfirmed"))
        or (item.get("modelOverride") is not None and not valid_model_id(item["modelOverride"]))
        for item in queued):
        raise ValueError("无效的排队请求")
    if "queuePaused" in value and not isinstance(value["queuePaused"], bool):
        raise ValueError("无效的队列状态")
    if "unreadCompletion" in value and not isinstance(value["unreadCompletion"], bool):
        raise ValueError("无效的未读完成状态")
    if value.get("nextModel") is not None and not valid_model_id(value["nextModel"]):
        raise ValueError("无效的会话模型选择")
    result = {key: item for key, item in value.items() if key in {
        "id", "title", "messages", "persona", "created", "updated", "sessionKey", "pendingTurnId",
        "archived", "pinned", "autonomyMode", "queued", "queuePaused", "unreadCompletion", "nextModel"}}
    result.update(title=title, updated=updated)
    if "historyWindow" in value:
        result["historyWindow"] = validate_history_window(value["historyWindow"])
    # Round-trip validates nested event values, rejects NaN, and prevents callers
    # retaining mutable references to the catalog transaction's input.
    return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))


class ChatHistoryStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "chat_catalog.sqlite3"
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS sessions "
                "(id TEXT PRIMARY KEY, updated REAL NOT NULL, payload TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)")
            connection.execute("CREATE TABLE IF NOT EXISTS turn_claims "
                "(turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload_hash TEXT NOT NULL, owner_pid INTEGER NOT NULL)")

    def _connect(self):
        # Journal-mode changes require a lock upgrade even for read consumers.
        # Preserve the file's existing mode; transaction/claim ownership does
        # not depend on repeatedly negotiating WAL during concurrent startup.
        return sqlite3.connect(self.path, timeout=10)

    def sessions(self, *, windowed: bool = False) -> list[dict]:
        with self._connect() as connection:
            sessions = [json.loads(row[0]) for row in connection.execute(
                "SELECT payload FROM sessions WHERE deleted=0 ORDER BY updated DESC, id")]
        return [project_history_window(session) for session in sessions] if windowed else sessions

    def session(self, session_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM sessions WHERE id=? AND deleted=0", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def older_messages(self, session_id: str, before: str, turns: int = HISTORY_WINDOW_TURNS) -> dict | None:
        session = self.session(session_id)
        return older_history_window(session, before, turns) if session is not None else None

    def sessions_page(self, limit: int, cursor: str | None = None, *, windowed: bool = False) -> dict:
        """Read by stable session ID so recent activity cannot shift the page boundary."""
        with self._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT id,payload FROM sessions WHERE deleted=0 AND id>? ORDER BY id LIMIT ?",
                (cursor or "", limit + 1),
            ).fetchall()
            next_cursor = rows[limit - 1][0] if len(rows) > limit else None
            # Read tombstones in the same snapshot as the final page.
            deleted = [] if next_cursor is not None else [row[0] for row in connection.execute(
                "SELECT id FROM sessions WHERE deleted=1 ORDER BY id")]
        sessions = [json.loads(row[1]) for row in rows[:limit]]
        return {"sessions": [project_history_window(session) for session in sessions] if windowed else sessions,
                "deletedIds": deleted, "nextCursor": next_cursor}

    def deleted_ids(self) -> list[str]:
        with self._connect() as connection:
            return [row[0] for row in connection.execute("SELECT id FROM sessions WHERE deleted=1 ORDER BY id")]

    def is_deleted(self, session_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM sessions WHERE id=? AND deleted=1", (session_id,)).fetchone() is not None

    def turn_claim(self, turn_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT session_id,payload_hash,owner_pid FROM turn_claims WHERE turn_id=?", (turn_id,)).fetchone()
            return {"sessionId": row[0], "payloadHash": row[1], "ownerPid": row[2]} if row else None

    def claim_turn(self, turn_id: str, session_id: str, payload_hash: str, event_path: Path) -> tuple[bool, dict]:
        """Claim once across tabs/processes; never truncate an existing job log."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT session_id,payload_hash,owner_pid FROM turn_claims WHERE turn_id=?", (turn_id,)).fetchone()
            if row:
                return False, {"sessionId": row[0], "payloadHash": row[1], "ownerPid": row[2]}
            if connection.execute("SELECT 1 FROM sessions WHERE id=? AND deleted=1", (session_id,)).fetchone():
                raise ChatSessionDeletedError("该会话已删除，请新建会话")
            event_path.parent.mkdir(parents=True, exist_ok=True)
            # A pre-existing unclaimed legacy log is uncertain, not permission to
            # execute the original action again.
            with event_path.open("x", encoding="utf-8"):
                pass
            connection.execute("INSERT INTO turn_claims VALUES(?,?,?,?)", (turn_id, session_id, payload_hash, os.getpid()))
        return True, {"sessionId": session_id, "payloadHash": payload_hash, "ownerPid": os.getpid()}

    def steer_receipt(self, request_id: str) -> dict | None:
        with self._connect() as connection:
            if not connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='steer_receipts'").fetchone():
                return None
            row = connection.execute(
                "SELECT payload_hash,status FROM steer_receipts WHERE request_id=?", (request_id,)).fetchone()
        return {"payloadHash": row[0], "status": row[1]} if row else None

    def claim_steer(self, request_id: str, payload_hash: str) -> bool:
        """Reserve a scoped request before its first and only native send."""
        with self._connect() as connection:
            # A legacy catalog can be open under an existing read transaction.
            # Only a real steer write should require this schema upgrade.
            connection.execute("CREATE TABLE IF NOT EXISTS steer_receipts "
                "(request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, status TEXT NOT NULL)")
            result = connection.execute(
                "INSERT OR IGNORE INTO steer_receipts VALUES(?,?,'unconfirmed')",
                (request_id, payload_hash))
            return result.rowcount == 1

    def finish_steer(self, request_id: str, payload_hash: str, status: str) -> None:
        if status not in ("accepted", "rejected"):
            raise ValueError("invalid steer receipt status")
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE steer_receipts SET status=? WHERE request_id=? AND payload_hash=? AND status='unconfirmed'",
                (status, request_id, payload_hash))
            if result.rowcount != 1:
                raise sqlite3.IntegrityError("steer reservation is no longer available")

    def merge(self, values: list[dict], *, windowed: bool = False) -> list[dict]:
        from web.chat_session_forks import has_forks, preserve_fork_prefix

        if not isinstance(values, list):
            raise ValueError("会话列表无效")
        sessions = [validated_session(value) for value in values]
        requested_starts = {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            fork_metadata_exists = has_forks(connection)
            for session in sessions:
                window = session.pop("historyWindow", None)
                if window is not None:
                    requested_starts[session["id"]] = window["start"]
                if window is not None or windowed:
                    row = connection.execute("SELECT updated,payload,deleted FROM sessions WHERE id=?",
                                             (session["id"],)).fetchone()
                    if row is not None:
                        updated, payload, deleted = row
                        if deleted or session["updated"] <= updated:
                            continue
                        current = json.loads(payload)
                        if window is not None:
                            candidate = merge_history_window(current, session, window)
                            if candidate is None:
                                continue
                            session = candidate
                        elif len(session["messages"]) < len(current["messages"]):
                            raise ChatHistoryWindowConflict("缺少原有历史，请刷新后再保存")
                    elif window is not None:
                        raise ChatHistoryWindowConflict("会话历史已不存在，请刷新后再保存")
                if fork_metadata_exists:
                    preserve_fork_prefix(connection, session)
                connection.execute("INSERT INTO sessions(id,updated,payload) VALUES(?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload "
                    "WHERE sessions.deleted=0 AND excluded.updated>sessions.updated",
                    (session["id"], session["updated"], json.dumps(session, ensure_ascii=False)))
        saved = self.sessions()
        return ([project_history_window(session, start=requested_starts.get(session["id"])) for session in saved]
                if windowed else saved)

    def delete(self, identifier: str) -> str:
        if (not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256
                or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in identifier)):
            raise ValueError("无效的会话标识")
        with self._connect() as connection:
            connection.execute("INSERT INTO sessions(id,updated,payload,deleted) VALUES(?,?,?,1) "
                "ON CONFLICT(id) DO UPDATE SET deleted=1,payload=excluded.payload,"
                "updated=MAX(sessions.updated,excluded.updated)",
                (identifier, int(time.time() * 1000), "{}"))
        return identifier
