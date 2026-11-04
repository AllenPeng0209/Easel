"""Durable review gate for exact, versioned chat plans.

The lifecycle follows DeepSeek Harness plan review (MIT, ce7073d). Easel
binds each plan and its one-shot execution claim to the authenticated scope.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class ChatPlanError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


MAX_PLAN_TEXT_BYTES = 64 * 1024
_SCOPE = "actor_id=? AND project_id=? AND session_id=?"
_REQUIRED_FIELDS = {
    "planId": "plan_id", "revision": "revision", "digest": "digest", "text": "text",
    "state": "state", "actorId": "actor_id", "projectId": "project_id", "sessionId": "session_id",
    "proposedTurnId": "proposed_turn_id", "callId": "call_id", "createdAt": "created_at",
}
_OPTIONAL_FIELDS = {
    "approvedAt": "approved_at", "executionTurnId": "execution_turn_id",
    "executionRequestId": "execution_request_id", "claimedAt": "claimed_at",
}


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _identity(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or re.search(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]", value)):
        raise ChatPlanError("计划范围或调用标识无效", 422)
    return value


def _scope(actor_id: str, project_id: str, session_id: str) -> tuple[str, str, str]:
    return _identity(actor_id), _identity(project_id), _identity(session_id)


def _digest(text: str) -> str:
    if not isinstance(text, str) or not text.strip() or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]", text):
        raise ChatPlanError("计划内容无效", 422)
    value = text.encode("utf-8")
    if len(value) > MAX_PLAN_TEXT_BYTES:
        raise ChatPlanError("计划内容超出长度限制", 422)
    return hashlib.sha256(value).hexdigest()


def _record(row: sqlite3.Row) -> dict:
    return {
        **{key: row[column] for key, column in _REQUIRED_FIELDS.items()},
        **{key: row[column] for key, column in _OPTIONAL_FIELDS.items() if row[column] is not None},
    }


class ChatPlanStore:
    def __init__(self, directory: Path):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "chat_plans.sqlite3"
        with self._database() as database:
            if database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_plans'").fetchone():
                return
        with self._database(write=True) as database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS chat_plans (
                    plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    actor_id TEXT NOT NULL, project_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    text TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL,
                    proposed_turn_id TEXT NOT NULL, call_id TEXT NOT NULL, created_at INTEGER NOT NULL,
                    approved_at INTEGER, execution_turn_id TEXT, execution_request_id TEXT, claimed_at INTEGER,
                    PRIMARY KEY(plan_id, revision),
                    UNIQUE(actor_id, project_id, session_id, revision),
                    UNIQUE(actor_id, project_id, session_id, proposed_turn_id, call_id),
                    UNIQUE(actor_id, project_id, execution_turn_id),
                    UNIQUE(actor_id, project_id, execution_request_id)
                )
            """)

    @contextmanager
    def _database(self, *, write=False):
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        try:
            if write:
                database.execute("BEGIN IMMEDIATE")
            with database:
                yield database
        finally:
            database.close()

    @staticmethod
    def _latest(database, scope) -> sqlite3.Row | None:
        return database.execute(f"SELECT * FROM chat_plans WHERE {_SCOPE} ORDER BY revision DESC LIMIT 1",
                                scope).fetchone()

    @staticmethod
    def _find(database, scope, plan_id, revision=None) -> sqlite3.Row:
        if not isinstance(plan_id, str) or not re.fullmatch(r"[a-f0-9]{32}", plan_id):
            raise ChatPlanError("计划不存在", 404)
        if revision is not None and (type(revision) is not int or not 1 <= revision < 2 ** 63):
            raise ChatPlanError("计划版本无效", 422)
        sql = f"SELECT * FROM chat_plans WHERE {_SCOPE} AND plan_id=?"
        values = (*scope, plan_id)
        if revision is not None:
            sql += " AND revision=?"
            values += (revision,)
        row = database.execute(sql + " ORDER BY revision DESC LIMIT 1", values).fetchone()
        if row is None:
            raise ChatPlanError("计划不存在", 404)
        return row

    @classmethod
    def _exact_latest(cls, database, scope, plan_id, revision, digest) -> sqlite3.Row:
        if revision is None:
            raise ChatPlanError("请确认具体计划版本", 422)
        row = cls._find(database, scope, plan_id, revision)
        if row["digest"] != digest or row["revision"] != cls._latest(database, scope)["revision"]:
            raise ChatPlanError("计划版本已更新，请核对最新方案")
        return row

    def propose(self, *, actor_id: str, project_id: str, session_id: str,
                text: str, turn_id: str, call_id: str, expected_plan_id: str | None = None,
                expected_revision: int | None = None, expected_digest: str | None = None,
                expected_state: str | None = None, require_empty: bool = False) -> dict:
        scope = _scope(actor_id, project_id, session_id)
        turn_id, call_id = _identity(turn_id), _identity(call_id)
        digest = _digest(text)
        expected = (expected_plan_id, expected_revision, expected_digest)
        has_expected = any(value is not None for value in expected)
        if ((has_expected and (require_empty or any(value is None for value in expected)))
                or (expected_state is not None and (not has_expected or expected_state not in ("proposed", "approved", "claimed")))):
            raise ChatPlanError("必须完整指定计划来源版本", 422)
        with self._database(write=True) as database:
            replay = database.execute(f"SELECT * FROM chat_plans WHERE {_SCOPE} AND proposed_turn_id=? AND call_id=?",
                                      (*scope, turn_id, call_id)).fetchone()
            if replay is not None:
                if replay["digest"] != digest:
                    raise ChatPlanError("同一计划调用不能更改内容")
                return _record(replay)
            previous = self._latest(database, scope)
            if require_empty and previous is not None:
                raise ChatPlanError("此会话已有方案，请核对最新版本")
            if has_expected:
                previous = self._exact_latest(database, scope, *expected)
                if expected_state is not None and previous["state"] != expected_state:
                    raise ChatPlanError("计划审批状态已变化，请核对后再修改")
            plan_id = previous["plan_id"] if previous else uuid.uuid4().hex
            revision = previous["revision"] + 1 if previous else 1
            if previous and previous["state"] in ("proposed", "approved"):
                database.execute("UPDATE chat_plans SET state='superseded' WHERE plan_id=? AND revision=?",
                                 (plan_id, previous["revision"]))
            database.execute(
                "INSERT INTO chat_plans(plan_id,revision,actor_id,project_id,session_id,text,digest,state,"
                "proposed_turn_id,call_id,created_at) VALUES(?,?,?,?,?,?,?,'proposed',?,?,?)",
                (plan_id, revision, *scope, text, digest, turn_id, call_id, _now_ms()),
            )
            return _record(self._find(database, scope, plan_id, revision))

    def get(self, *, actor_id: str, project_id: str, session_id: str, plan_id: str, revision: int | None = None) -> dict:
        scope = _scope(actor_id, project_id, session_id)
        with self._database() as database:
            return _record(self._find(database, scope, plan_id, revision))

    def latest(self, *, actor_id: str, project_id: str, session_id: str) -> dict | None:
        scope = _scope(actor_id, project_id, session_id)
        with self._database() as database:
            row = self._latest(database, scope)
            return _record(row) if row else None

    def history(self, *, actor_id: str, project_id: str, session_id: str) -> list[dict]:
        scope = _scope(actor_id, project_id, session_id)
        with self._database() as database:
            return [_record(row) for row in database.execute(
                f"SELECT * FROM chat_plans WHERE {_SCOPE} ORDER BY revision", scope)]

    def approve(self, *, actor_id: str, project_id: str, session_id: str,
                plan_id: str, revision: int, digest: str) -> dict:
        scope = _scope(actor_id, project_id, session_id)
        with self._database(write=True) as database:
            row = self._exact_latest(database, scope, plan_id, revision, digest)
            if row["state"] == "approved":
                return _record(row)
            if row["state"] != "proposed":
                raise ChatPlanError("计划已经结束审批")
            database.execute("UPDATE chat_plans SET state='approved', approved_at=? WHERE plan_id=? AND revision=?",
                             (_now_ms(), plan_id, revision))
            return _record(self._find(database, scope, plan_id, revision))

    def claim_execution(self, *, actor_id: str, project_id: str, session_id: str,
                        plan_id: str, revision: int, digest: str, turn_id: str, request_id: str) -> dict:
        scope = _scope(actor_id, project_id, session_id)
        turn_id, request_id = _identity(turn_id), _identity(request_id)
        with self._database(write=True) as database:
            row = self._exact_latest(database, scope, plan_id, revision, digest)
            # The chat journal's own exact-turn claim is the dispatch fence.
            # The same request may recover a plan claim after a host crash.
            if (row["state"] == "claimed" and row["execution_turn_id"] == turn_id
                    and row["execution_request_id"] == request_id):
                return _record(row)
            if row["state"] != "approved" or row["proposed_turn_id"] == turn_id:
                raise ChatPlanError("该计划未获执行批准或执行凭据已使用")
            try:
                database.execute("UPDATE chat_plans SET state='claimed', execution_turn_id=?, execution_request_id=?,"
                                 " claimed_at=? WHERE plan_id=? AND revision=?",
                                 (turn_id, request_id, _now_ms(), plan_id, revision))
            except sqlite3.IntegrityError:
                raise ChatPlanError("该执行回合或请求已被使用") from None
            return _record(self._find(database, scope, plan_id, revision))
