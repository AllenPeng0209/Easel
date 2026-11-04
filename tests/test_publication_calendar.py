"""A confirmed operation projects to the local calendar at most once."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import subprocess

import pytest


@pytest.fixture
def calendar(monkeypatch, tmp_path):
    def denied(*args, **kwargs):
        pytest.fail('Calendar projection tests cannot call a platform or subprocess')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.delenv('EASEL_CALENDAR_AUTORECORD', raising=False)
    source = Path(__file__).resolve().parents[1] / 'skills/shared/scripts/calendar_ops.py'
    spec = importlib.util.spec_from_file_location('publication_calendar_projection_test', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    forwards = []
    monkeypatch.setattr(module, '_forward_publish_log', lambda *args, **kwargs: forwards.append(args))
    return module, tmp_path / '_schedule.json', forwards


def test_one_operation_replay_keeps_one_calendar_row_and_one_forward(calendar):
    module, path, forwards = calendar
    assert module.record_publish('douyin', 'Confirmed title', url='https://self.test/post', data_path=path,
                                 operation_id='a' * 32, payload_hash='b' * 64)
    original = path.read_bytes()
    assert module.record_publish('douyin', 'Replay must not replace title', url='https://self.test/different', data_path=path,
                                 operation_id='a' * 32, payload_hash='b' * 64) is False
    assert path.read_bytes() == original
    items = json.loads(original)
    assert len(items) == 1
    assert items[0]['publicationOperationId'] == 'a' * 32
    assert items[0]['publicationPayloadHash'] == 'b' * 64
    assert items[0]['status'] == 'published'
    assert len(forwards) == 1


def test_conflicting_operation_hash_never_overwrites_or_forwards(calendar):
    module, path, forwards = calendar
    assert module.record_publish('zhihu', 'Original', data_path=path,
                                 operation_id='a' * 32, payload_hash='b' * 64)
    original = path.read_bytes()
    assert module.record_publish('zhihu', 'Changed', data_path=path,
                                 operation_id='a' * 32, payload_hash='c' * 64) is False
    assert path.read_bytes() == original
    assert len(forwards) == 1


def test_distinct_operations_can_project_the_same_content_separately(calendar):
    module, path, forwards = calendar
    for operation in ['a' * 32, 'c' * 32]:
        assert module.record_publish('bilibili', 'Same content', data_path=path,
                                     operation_id=operation, payload_hash='b' * 64)
    assert {row['publicationOperationId'] for row in json.loads(path.read_text())} == {'a' * 32, 'c' * 32}
    assert len(forwards) == 2


@pytest.mark.parametrize('operation,payload', [('', 'b' * 64), ('a' * 32, ''),
    ('a' * 31, 'b' * 64), ('a' * 32, 'b' * 63), ('g' * 32, 'b' * 64), ('a' * 32, 'z' * 64),
    (None, None), (True, 'b' * 64), ('a' * 32, 123)])
def test_invalid_correlation_never_creates_calendar_or_forward(calendar, operation, payload):
    module, path, forwards = calendar
    assert module.record_publish('douyin', 'Invalid binding', data_path=path,
                                 operation_id=operation, payload_hash=payload) is False
    assert not path.exists()
    assert forwards == []


def test_legacy_calls_remain_available_without_operation_metadata(calendar):
    module, path, forwards = calendar
    assert module.record_publish('zhihu', 'Legacy', data_path=path)
    assert module.record_publish('zhihu', 'Legacy', data_path=path)
    rows = json.loads(path.read_text())
    assert len(rows) == 2
    assert all('publicationOperationId' not in row and 'publicationPayloadHash' not in row for row in rows)
    assert len(forwards) == 2
