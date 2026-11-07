"""Lossless private tool-detail storage with bounded transport projections."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

_REFERENCE = re.compile(r'[a-f0-9]{64}')


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _preview(value, remaining=None, depth=0):
    remaining = [700] if remaining is None else remaining
    if remaining[0] <= 0 or depth > 3:
        return '…'
    if isinstance(value, str):
        count = min(len(value), 512, remaining[0])
        remaining[0] -= count
        return value[:count] + ('…' if count < len(value) else '')
    if isinstance(value, dict):
        return {key: _preview(item, remaining, depth+1) for key, item in list(value.items())[:8]}
    if isinstance(value, list):
        return [_preview(item, remaining, depth+1) for item in value[:6]]
    return value


class EventDetails:
    def __init__(self, sessions: Path):
        self.directory = Path(sessions) / 'event-details'

    def _path(self, reference: str) -> Path:
        if not isinstance(reference, str) or _REFERENCE.fullmatch(reference) is None:
            raise ValueError('Invalid tool detail reference')
        if self.directory.is_symlink():
            raise ValueError('Invalid tool detail directory')
        path = self.directory / (reference + '.json')
        if path.is_symlink():
            raise ValueError('Invalid tool detail file')
        return path

    def get(self, reference: str) -> dict:
        data = self._path(reference).read_bytes()
        if hashlib.sha256(data).hexdigest() != reference:
            raise ValueError('Tool detail checksum mismatch')
        event = json.loads(data)
        if not isinstance(event, dict):
            raise ValueError('Invalid tool details')
        return event

    def compact_event(self, event: dict) -> dict:
        # Host business/approval records stay typed and immediately available.
        if not isinstance(event, dict) or event.get('kind') != 'tool' or event.get('detailRef'):
            return event
        data = _encode(event)
        if len(data) <= 2048:
            return event
        reference = hashlib.sha256(data).hexdigest()
        target = self._path(reference)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            descriptor, name = tempfile.mkstemp(dir=self.directory, prefix='.event-', suffix='.tmp')
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(data)
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
        result = {**event, 'detailRef': reference, 'detailsAvailable': True}
        budget = [700]
        for key in ('input', 'output', 'text'):
            if key in result:
                result[key] = _preview(result[key], budget)
        return result

    def restore_event(self, event: dict) -> dict:
        if not isinstance(event, dict) or not event.get('detailRef'):
            return event
        original = self.get(event['detailRef'])
        if any(event.get(key) != original.get(key) for key in ('id', 'kind', 'runId', 'callId')):
            raise ValueError('Tool detail identity does not match the session event')
        # Frontend reducers may have filled earlier timestamps on a complete
        # snapshot. Preserve that context while the stored body stays authoritative.
        result = {**event, **original}
        result.pop('detailRef', None)
        result.pop('detailsAvailable', None)
        return result

    def _sessions(self, sessions, transform):
        return [{**session, 'messages': [{**message, **({'events': [transform(event) for event in message['events']]}
                 if isinstance(message.get('events'), list) else {})} for message in session['messages']]}
                for session in sessions]

    def compact_sessions(self, sessions):
        return self._sessions(sessions, self.compact_event)

    def restore_sessions(self, sessions):
        return self._sessions(sessions, self.restore_event)
