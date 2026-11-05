"""C17 technical session ZIP, adapted from Harness session-log-export.

Read the committed scoped journal in order and serialize a canonical JSONL
projection. Xiangya journals are distinct from native engine and readable logs.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing, contextmanager
from itertools import islice

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from easel.projects import project_id
from easel.request_identity import current_user
from web.chat_history import ChatHistoryStore
from web.chat_session_references import valid_session_reference_id

_TURN_ID = re.compile(r"[A-Za-z0-9_.-]{1,160}")
_LINE_BYTES = 1024 * 1024
_CHUNK = 64 * 1024
_LEGACY_SCAN = 10_000
_EVENTS = frozenset({"admitted", "token", "thinking", "reasoning_summary", "answer_completed", "activity",
                     "agent_event", "question", "question_closed", "error", "done", "heartbeat", "ping"})
_KINDS = frozenset({"tool", "plan", "usage", "context", "decision", "artifact", "task", "publication",
                    "business", "steer", "steer_capability", "subagent"})
_STATUSES = frozenset({"preparing", "running", "pending", "completed", "failed", "interrupted", "declined",
                       "cancelled", "stopped", "unconfirmed", "expired", "answered", "resolved", "blocked",
                       "replaced", "revoked", "closed", "available", "unavailable", "accepted", "ready",
                       "submitted", "confirmed", "draft_created", "exported", "scheduled", "approved",
                       "rejected", "invalidated", "executing", "unknown"})
_ERROR_CODES = frozenset({"authentication", "model_unavailable", "rate_limit", "quota_exhausted", "network",
                          "timeout", "permission_denied", "session_conflict", "storage", "unknown"})
_TOOL_NAMES = frozenset({"bash", "apply_patch", "web_search", "read_image", "present",
                         "easel_ask_user", "easel_business_objects", "easel_publish", "easel_publication_status"})


def _row_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _safe_enum(value, choices):
    return value if isinstance(value, str) and value in choices else None


def _ref(session: str, domain: str, value: str) -> str:
    user = current_user()
    return hashlib.sha256(_row_bytes([user.get("id") if user else None, project_id(), session, domain, value])).hexdigest()


def _safe_agent_data(data: dict, session: str) -> dict:
    kind = _safe_enum(data.get("kind"), _KINDS)
    if kind is None:
        return {"kind": "unknown"}
    projected = {"kind": kind}
    name = _safe_enum(data.get("name"), _TOOL_NAMES)
    status = _safe_enum(data.get("status"), _STATUSES)
    if name is not None:
        projected["name"] = name
    if status is not None:
        projected["status"] = status
    for key, domain in (("id", "event"), ("runId", "run"), ("callId", "call"), ("parentCallId", "call"),
                        ("questionId", "question"), ("requestId", "request")):
        value = data.get(key)
        if isinstance(value, str) and 0 < len(value) <= 512:
            projected[key] = _ref(session, domain, value)
    for key in ("startedAt", "completedAt", "durationMs", "timestamp", "exitCode"):
        value = data.get(key)
        if type(value) is int and -(2**31) <= value < 2**53:
            projected[key] = value
        elif key == "exitCode" and key in data and value is None:
            projected[key] = None
    for key in ("truncated", "fileChangeApplied", "nativeWebSearch"):
        if type(data.get(key)) is bool:
            projected[key] = data[key]
    if kind == "publication" and isinstance(data.get("publication"), dict):
        publication = data["publication"]
        state = _safe_enum(publication.get("state"), _STATUSES)
        if state is not None:
            projected["publicationState"] = state
        result = publication.get("result")
        result_status = _safe_enum(result.get("status"), _STATUSES) if isinstance(result, dict) else None
        if result_status is not None:
            projected["publicationResult"] = result_status
    return projected


def _safe_event(record, session, turn_ref):
    kind = _safe_enum(record["event"], _EVENTS) or "unknown"
    data = record.get("data")
    projected = {}
    if kind in ("question", "question_closed") and isinstance(data, str):
        data = _parse(data)
    if isinstance(data, dict):
        if kind == "agent_event":
            projected = _safe_agent_data(data, session)
        elif kind == "done":
            outcome = _safe_enum(data.get("outcome"), _STATUSES)
            if outcome:
                projected["outcome"] = outcome
        elif kind == "error":
            for field, choices in (("code", _ERROR_CODES), ("phase", ("startup", "execution", "finalization"))):
                value = _safe_enum(data.get(field), choices)
                if value:
                    projected[field] = value
            for field in ("retryable", "unconfirmed"):
                if type(data.get(field)) is bool:
                    projected[field] = data[field]
        elif kind in ("question", "question_closed", "answer_completed"):
            field = "itemId" if kind == "answer_completed" else "id"
            value = data.get(field)
            if isinstance(value, str) and 0 < len(value) <= 512:
                projected[field] = _ref(session, "item" if field == "itemId" else "question", value)
            status = _safe_enum(data.get("status"), _STATUSES)
            if status:
                projected["status"] = status
        elif kind == "reasoning_summary" and type(data.get("truncated")) is bool:
            projected["truncated"] = data["truncated"]
    return {"turnRef": turn_ref, "id": record["id"], "event": kind, "data": projected}


def _parse(line):
    try:
        value = json.loads(line)
        return value if isinstance(value, dict) else None
    except (UnicodeError, ValueError, RecursionError):
        return None


def _supports_protected_read() -> bool:
    return os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW')


@contextmanager
def _job_directory(directory):
    if not _supports_protected_read():
        yield None
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root = None
    jobs = None
    try:
        try:
            root = os.open(directory, flags)
            jobs = os.open('jobs', flags, dir_fd=root)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                raise
        yield jobs
    finally:
        if jobs is not None:
            os.close(jobs)
        if root is not None:
            os.close(root)


@contextmanager
def _journal(directory, turn_id):
    if directory is None or not isinstance(turn_id, str) or not _TURN_ID.fullmatch(turn_id):
        yield None
        return
    try:
        descriptor = os.open(f"{turn_id}.jsonl", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError as error:
        if error.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            raise
        yield None
        return
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        yield (stream, info.st_size) if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 else None


def _belongs(stream, size, session, turn, *, claimed):
    stream.seek(0)
    first = stream.readline(min(size, _LINE_BYTES))
    initial = _parse(first) if first.endswith(b"\n") else None
    admitted = initial and initial.get("event") == "admitted"
    if admitted and (initial.get("id") != 1 or initial.get("data") != {"sessionId": session, "turnId": turn}):
        return False
    offset = max(0, size - _CHUNK)
    stream.seek(offset)
    tail = stream.read(size - offset).split(b"\n")
    if offset:
        tail = tail[1:]
    # Never inspect the potentially torn line at the captured end.
    last = next((_parse(line) for line in reversed(tail[:-1]) if line.strip()), None)
    terminal = last and last.get("event") == "done" and isinstance(last.get("data"), dict)
    terminal_session = last["data"].get("sessionKey") if terminal else None
    if terminal_session is not None and terminal_session != session:
        return False
    return bool(claimed or admitted or terminal_session == session)


def _records(stream, size, session, turn, counts):
    stream.seek(0)
    last_id = 0
    turn_ref = _ref(session, "turn", turn)
    while stream.tell() < size:
        line = stream.readline(min(_LINE_BYTES + 1, size - stream.tell()))
        if not line:
            counts["incompleteLines"] += 1
            break
        if len(line) > _LINE_BYTES:
            counts["oversizeLines"] += 1
            while not line.endswith(b"\n") and stream.tell() < size:
                line = stream.readline(min(_CHUNK, size - stream.tell()))
                if not line:
                    break
            continue
        if not line.endswith(b"\n"):
            counts["incompleteLines"] += 1
            break
        record = _parse(line)
        identifier = record.get("id") if record else None
        if type(identifier) is not int or not last_id < identifier < 2**53 or not isinstance(record.get("event"), str):
            counts["invalidLines"] += 1
            continue
        last_id = identifier
        if record["event"] == "admitted" and (identifier != 1 or record.get("data") != {"sessionId": session, "turnId": turn}):
            counts["conflictingAdmissionRecords"] += 1
            continue
        counts["events"] += 1
        yield _row_bytes(_safe_event(record, session, turn_ref))


def _legacy_turns(directory, counts):
    if directory is None or os.scandir not in os.supports_fd:
        return []
    with os.scandir(directory) as entries:
        observed = [entry.name for entry in islice(entries, _LEGACY_SCAN + 1)]
    counts["legacyDiscoveryLimited"] = len(observed) > _LEGACY_SCAN
    return sorted(name[:-6] for name in observed[:_LEGACY_SCAN]
                  if name.endswith(".jsonl") and _TURN_ID.fullmatch(name[:-6]))


def _visible_session(catalog, session):
    with closing(catalog._connect()) as database:
        return database.execute("SELECT 1 FROM sessions WHERE id=? AND deleted=0", (session,)).fetchone() is not None


def _journal_records(catalog, session, counts):
    with closing(catalog._connect()) as database, _job_directory(catalog.path.parent) as directory:
        counts["directoryAvailable"] = directory is not None

        def one(turn, claimed):
            with _journal(directory, turn) as opened:
                if opened is None:
                    if claimed:
                        counts["missingClaimedJournals"] += 1
                    return
                stream, size = opened
                if not _belongs(stream, size, session, turn, claimed=claimed):
                    if claimed:
                        counts["conflictingJournals"] += 1
                    return
                events_before = counts["events"]
                yield from _records(stream, size, session, turn, counts)
                if counts["events"] > events_before:
                    counts["exportedTurns"] += 1
                    if not claimed:
                        counts["legacyTurns"] += 1
                elif claimed:
                    counts["emptyClaimedJournals"] += 1

        # Admission rows are immutable. Page a captured upper rowid without
        # holding a database read transaction while reading/compressing logs.
        upper = database.execute("SELECT COALESCE(MAX(rowid),0) FROM turn_claims WHERE session_id=?", (session,)).fetchone()[0]
        cursor = 0
        while cursor < upper:
            claims = database.execute("SELECT rowid,turn_id FROM turn_claims WHERE session_id=? AND rowid>? AND rowid<=? "
                                      "ORDER BY rowid LIMIT 100", (session, cursor, upper)).fetchall()
            if not claims:
                break
            for cursor, turn in claims:
                yield from one(turn, True)
        for turn in _legacy_turns(directory, counts):
            # Claim evidence wins over a file's legacy terminal marker, even
            # for a claim committed after the exported upper bound.
            if database.execute("SELECT 1 FROM turn_claims WHERE turn_id=?", (turn,)).fetchone() is None:
                yield from one(turn, False)


def _build_archive(file, catalog, session):
    counts = {"exportedTurns": 0, "legacyTurns": 0, "events": 0, "missingClaimedJournals": 0,
              "emptyClaimedJournals": 0, "conflictingJournals": 0, "invalidLines": 0,
              "incompleteLines": 0, "oversizeLines": 0, "conflictingAdmissionRecords": 0,
              "legacyDiscoveryLimited": False, "protectedDirectoryReadSupported": _supports_protected_read(),
              "legacyDiscoveryAvailable": os.scandir in os.supports_fd}
    with zipfile.ZipFile(file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        records = _journal_records(catalog, session, counts)
        try:
            first = next(records, None)
            if first is not None:
                with archive.open("journal.jsonl", "w", force_zip64=True) as journal:
                    journal.write(first)
                    for line in records:
                        journal.write(line)
        finally:
            records.close()
        archive.writestr("manifest.json", _row_bytes({
            "schemaVersion": 1, "format": "easel-redacted-session-journal",
            "includesReadableTranscript": False,
            "journal": {"source": "scoped_stream_journal", "path": "journal.jsonl" if counts["events"] else None, **counts},
            "excluded": ["free_form_text", "raw_provider_payloads", "native_engine_history", "attachments", "readable_transcript"],
            "identifierEncoding": "session_scoped_sha256",
        }))
    return file.tell()


def create_router(directory_provider, *, require_admin) -> APIRouter:
    router = APIRouter(prefix="/api/chat/sessions", tags=["chat-session-export"])

    @router.get("/{session_id}/export/logs")
    @router.head("/{session_id}/export/logs")
    def export_logs(request: Request, session_id: str):
        def authorize():
            user = current_user()
            if not user or user.get("is_admin") is not True:
                raise HTTPException(403, "仅平台管理员可以导出技术日志")
            require_admin()
            fresh = getattr(request.state, "chat_export_authorized", None)
            if fresh is not None and not fresh():
                raise HTTPException(403, "当前权限已变更")

        authorize()
        if not valid_session_reference_id(session_id):
            raise HTTPException(404, "会话不存在或无权访问")
        name = _ref(session_id, "download", session_id)[:16]
        headers = {"Content-Disposition": f'attachment; filename="xiangya-session-{name}.zip"',
                   "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        file = None
        try:
            catalog = ChatHistoryStore(directory_provider())
            if not _visible_session(catalog, session_id):
                raise HTTPException(404, "会话不存在或无权访问")
            if request.method == "HEAD":
                authorize()
                response = Response(media_type="application/zip", headers=headers)
                del response.headers["Content-Length"]
                return response
            file = tempfile.TemporaryFile("w+b")
            size = _build_archive(file, catalog, session_id)
            authorize()
            if not _visible_session(catalog, session_id):
                raise HTTPException(404, "会话不存在或无权访问")
            file.seek(0)
        except BaseException as error:
            if file is not None:
                file.close()
            if isinstance(error, (OSError, sqlite3.Error)):
                raise HTTPException(503, "暂时无法读取技术日志，请重试") from None
            raise

        def stream():
            try:
                while chunk := file.read(_CHUNK):
                    yield chunk
            finally:
                file.close()

        return StreamingResponse(stream(), media_type="application/zip", background=BackgroundTask(file.close),
                                 headers={**headers, "Content-Length": str(size)})

    return router
