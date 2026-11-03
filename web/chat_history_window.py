"""Turn-aligned catalog history windows, following Harness session-controller.

The cursor names a cut in one durable history snapshot. Unlike a browser-only
offset, the base digest also guards writes against a changed loaded suffix.
"""
from __future__ import annotations

import hashlib
import json
import re

HISTORY_WINDOW_TURNS = 20
_CURSOR = re.compile(r"w1\.([1-9][0-9]{0,15})\.([a-f0-9]{64})")
_DIGEST = re.compile(r"[a-f0-9]{64}")


class ChatHistoryWindowConflict(ValueError):
    """The saved browser view no longer matches its authoritative base."""


def _start_of_window(messages: list[dict], end: int, turns: int) -> int:
    selected = None
    for index in range(end - 1, -1, -1):
        if messages[index]["role"] != "user":
            continue
        if selected is not None:
            return selected
        turns -= 1
        if turns == 0:
            selected = index
    # Include any assistant-only legacy introduction with the oldest page.
    return 0


def _turn_boundary(messages: list[dict], start: int) -> bool:
    return start == 0 or 0 < start < len(messages) and messages[start]["role"] == "user"


def _descriptor(session: dict, start: int) -> dict:
    encoded = json.dumps([session["id"], start, session["messages"]], sort_keys=True,
                         ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    base = hashlib.sha256(encoded).hexdigest()
    return {"start": start, "base": base, "before": f"w1.{start}.{base}" if start else None}


def validate_history_window(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"start", "before", "base"}:
        raise ValueError("无效的会话历史窗口")
    start, before, base = value["start"], value["before"], value["base"]
    if (type(start) is not int or not 0 <= start < 2**53 or not isinstance(base, str) or not _DIGEST.fullmatch(base)
            or before != (f"w1.{start}.{base}" if start else None)):
        raise ValueError("无效的会话历史窗口")
    return value


def project_history_window(session: dict, turns: int = HISTORY_WINDOW_TURNS, *, start: int | None = None) -> dict:
    messages = session["messages"]
    if start is None or not _turn_boundary(messages, start):
        start = _start_of_window(messages, len(messages), turns)
    return {**session, "messages": messages[start:], "historyWindow": _descriptor(session, start)}


def older_history_window(session: dict, before: str, turns: int = HISTORY_WINDOW_TURNS) -> dict:
    cursor = _CURSOR.fullmatch(before) if isinstance(before, str) else None
    if cursor is None:
        raise ChatHistoryWindowConflict("历史分页位置无效，请刷新对话")
    end = int(cursor[1])
    messages = session["messages"]
    if not _turn_boundary(messages, end) or before != _descriptor(session, end)["before"]:
        raise ChatHistoryWindowConflict("会话历史已更新，请刷新后继续加载")
    start = _start_of_window(messages, end, turns)
    return {"messages": messages[start:end], "historyWindow": _descriptor(session, start)}


def merge_history_window(current: dict, incoming: dict, window: dict) -> dict | None:
    """Rebuild a windowed write against the durable row inside its transaction."""
    start = window["start"]
    stored = current["messages"]
    suffix = incoming["messages"]
    if not _turn_boundary(stored, start):
        raise ChatHistoryWindowConflict("会话历史已更新，请刷新后再保存")
    candidate = {**incoming, "messages": [*stored[:start], *suffix]}
    metadata = {"updated", "fork"}  # The server restores fork provenance separately.
    if ({key: value for key, value in current.items() if key not in metadata}
            == {key: value for key, value in candidate.items() if key not in metadata}):
        return None
    if (window != _descriptor(current, start) or len(suffix) < len(stored) - start
            or start and suffix[0]["role"] != "user"):
        raise ChatHistoryWindowConflict("会话历史已更新，请刷新后再保存")
    return candidate
