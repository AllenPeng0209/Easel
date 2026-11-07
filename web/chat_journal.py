"""Bounded readers for append-only chat journals. No chat execution or writes."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


MAX_RECORD_BYTES = 64 * 1024 * 1024


class JournalCursor:
    """Keep a byte offset, rather than re-decoding an entire turn every poll.

    One unusually large event can exceed the batch byte target, but not the
    record ceiling. A partial final line is retried without consuming it.
    Replacing/truncating a journal fails closed; it never restarts the agent.
    """

    def __init__(self, path: Path, *, offset: int = 0, identity=None,
                 max_events: int = 128, max_bytes: int = 1024 * 1024):
        self.path = Path(path)
        self.offset = offset
        self.identity = identity
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.eof = False
        self.positions: list[int] = []

    def read_batch(self) -> list[dict]:
        events, self.positions = [], []
        self.eof = False
        with self.path.open('rb') as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if self.identity is not None and self.identity != identity:
                raise ValueError('Journal was replaced')
            if stat.st_size < self.offset:
                raise ValueError('Journal was truncated')
            self.identity = identity
            stream.seek(self.offset)
            consumed = 0
            while len(events) < self.max_events and consumed < self.max_bytes:
                start = stream.tell()
                line = stream.readline(MAX_RECORD_BYTES + 1)
                if not line:
                    self.eof = True
                    break
                if len(line) > MAX_RECORD_BYTES:
                    raise ValueError('Journal event exceeds reader limit')
                if not line.endswith(b'\n'):
                    # Even a complete-looking JSON object is not committed
                    # until its JSONL delimiter arrives. Keep the byte offset.
                    self.eof = True
                    break
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    event = None
                self.offset = stream.tell()
                consumed += len(line)
                if (not isinstance(event, dict) or type(event.get('id')) is not int
                        or event['id'] <= 0 or not isinstance(event.get('event'), str)
                        or not event['event']):
                    continue
                self.positions.append(start)
                events.append(event)
            if self.offset >= stat.st_size:
                self.eof = True
        return events


@dataclass
class JournalSummary:
    identity: tuple[int, int] | None
    replay_offset: int
    legacy_replay_offset: int
    has_admission: bool
    has_done: bool
    has_error: bool
    error_before_cursor: bool
    legacy_error_before_cursor: bool
    legacy_recoverable: bool


def scan_journal(path: Path, *, after: int = 0, admission: dict | None = None) -> JournalSummary:
    """Scan once, retaining only bounded batches and admission/terminal facts.

    The returned replay offsets avoid parsing the skipped prefix a second time.
    Callers run this and each subsequent read outside the ASGI event loop.
    """
    reader = JournalCursor(path)
    count = 0
    has_admission = has_done = has_error = error_before = legacy_error_before = False
    contiguous = True
    last_kind = last_session = None
    replay_offset = legacy_offset = None
    while not reader.eof:
        batch = reader.read_batch()
        for offset, event in zip(reader.positions, batch):
            if count == 0:
                has_admission = admission is not None and event == admission
            elif last_kind in ('admitted', 'done'):
                contiguous = False
            count += 1
            contiguous = contiguous and event['id'] == count
            kind = event['event']
            has_done = has_done or kind == 'done'
            has_error = has_error or kind == 'error'
            error_before = error_before or (kind == 'error' and event['id'] <= after)
            legacy_error_before = legacy_error_before or (kind == 'error' and event['id'] + 1 <= after)
            if replay_offset is None and event['id'] > after:
                replay_offset = offset
            if legacy_offset is None and event['id'] + 1 > after:
                legacy_offset = offset
            data = event.get('data')
            last_kind = kind
            last_session = data.get('sessionKey') if isinstance(data, dict) else None
    return JournalSummary(
        identity=reader.identity,
        replay_offset=reader.offset if replay_offset is None else replay_offset,
        legacy_replay_offset=reader.offset if legacy_offset is None else legacy_offset,
        has_admission=has_admission, has_done=has_done, has_error=has_error,
        error_before_cursor=error_before, legacy_error_before_cursor=legacy_error_before,
        legacy_recoverable=bool(admission is not None and not has_admission and contiguous
            and last_kind == 'done' and last_session == admission['data']['sessionId']),
    )
