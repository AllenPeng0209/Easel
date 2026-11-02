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
        for item in queued):
        raise ValueError("无效的排队请求")
    if "queuePaused" in value and not isinstance(value["queuePaused"], bool):
        raise ValueError("无效的队列状态")
    result = {key: item for key, item in value.items() if key in {
        "id", "title", "messages", "persona", "created", "updated", "sessionKey", "pendingTurnId",
        "archived", "pinned", "autonomyMode", "queued", "queuePaused"}}
    result.update(title=title, updated=updated)
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

    def sessions(self) -> list[dict]:
        with self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT payload FROM sessions WHERE deleted=0 ORDER BY updated DESC, id")]

    def deleted_ids(self) -> list[str]:
        with self._connect() as connection:
            return [row[0] for row in connection.execute("SELECT id FROM sessions WHERE deleted=1 ORDER BY id")]

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
            event_path.parent.mkdir(parents=True, exist_ok=True)
            # A pre-existing unclaimed legacy log is uncertain, not permission to
            # execute the original action again.
            with event_path.open("x", encoding="utf-8"):
                pass
            connection.execute("INSERT INTO turn_claims VALUES(?,?,?,?)", (turn_id, session_id, payload_hash, os.getpid()))
        return True, {"sessionId": session_id, "payloadHash": payload_hash, "ownerPid": os.getpid()}

    def merge(self, values: list[dict]) -> list[dict]:
        if not isinstance(values, list):
            raise ValueError("会话列表无效")
        sessions = [validated_session(value) for value in values]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for session in sessions:
                connection.execute("INSERT INTO sessions(id,updated,payload) VALUES(?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload "
                    "WHERE sessions.deleted=0 AND excluded.updated>sessions.updated",
                    (session["id"], session["updated"], json.dumps(session, ensure_ascii=False)))
        return self.sessions()

    def delete(self, identifier: str) -> None:
        if not identifier or len(identifier) > 256:
            raise ValueError("无效的会话标识")
        with self._connect() as connection:
            connection.execute("INSERT INTO sessions(id,updated,payload,deleted) VALUES(?,?,?,1) "
                "ON CONFLICT(id) DO UPDATE SET deleted=1,updated=MAX(sessions.updated,excluded.updated)",
                (identifier, int(time.time() * 1000), "{}"))
