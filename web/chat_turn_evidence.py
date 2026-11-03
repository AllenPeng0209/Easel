"""Scope and terminal proof shared by completed-turn references and forks."""
from __future__ import annotations

import json
import os
import re
import stat

CATALOG_TURN_ID = r"[A-Za-z0-9_.-]{1,160}"
_JOURNAL_TAIL_BYTES = 64 * 1024


def valid_catalog_turn_id(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(CATALOG_TURN_ID, value))


def completed_catalog_turn(catalog, database, source_id: str, turn_id: str) -> bool:
    if not valid_catalog_turn_id(turn_id):
        return False
    claim = database.execute("SELECT session_id FROM turn_claims WHERE turn_id=?", (turn_id,)).fetchone()
    if not claim or claim[0] != source_id:
        return False
    path = catalog.path.parent / "jobs" / f"{turn_id}.jsonl"
    if path.is_symlink() or path.parent.is_symlink():
        return False
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                descriptor = os.open(path.name, flags, dir_fd=directory)
            finally:
                os.close(directory)
        else:
            descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as journal:
            info = os.fstat(journal.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return False
            first = journal.readline(min(info.st_size, _JOURNAL_TAIL_BYTES))
            if first.endswith(b"\n"):
                initial = json.loads(first)
                if isinstance(initial, dict) and initial.get("event") == "admitted" and (
                        initial.get("id") != 1 or initial.get("data") != {"sessionId": source_id, "turnId": turn_id}):
                    return False
            start = max(0, info.st_size - _JOURNAL_TAIL_BYTES)
            journal.seek(start)
            tail = journal.read(info.st_size - start).split(b"\n")
        complete = tail[1:-1] if start else tail[:-1]
        last = json.loads(next(line for line in reversed(complete) if line.strip()))
        return (isinstance(last, dict) and type(last.get("id")) is int and last["id"] > 0
                and last.get("event") == "done" and isinstance(last.get("data"), dict)
                and last["data"].get("outcome") == "completed" and last["data"].get("sessionKey") == source_id)
    except (OSError, ValueError, StopIteration, RecursionError):
        return False
