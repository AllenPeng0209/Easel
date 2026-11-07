import asyncio
import json

import pytest

from tests.test_chat_event_details import large_event

pytestmark = pytest.mark.access_integration


def test_compact_replay_keeps_raw_journal_and_serves_bounded_tool_snapshots(tmp_path, monkeypatch):
    from web import app as web
    from web.chat_event_details import EventDetails
    monkeypatch.setattr(web, '_sessions_dir', lambda: tmp_path)
    monkeypatch.setattr(web, '_ACTIVE_TURN_IDS', set())
    turn = 'compact-stream-test'
    path = web._job_event_file(turn)
    path.parent.mkdir(parents=True, exist_ok=True)
    web.ChatHistoryStore(tmp_path).claim_turn(turn, 'chat-one', 'hash', path)
    original = large_event()
    journal = [
        {'id': 1, 'event': 'admitted', 'data': {'turnId': turn, 'sessionId': 'chat-one'}},
        {'id': 2, 'event': 'agent_event', 'data': original},
        {'id': 3, 'event': 'done', 'data': {'sessionKey': 'chat-one'}},
    ]
    path.write_text(''.join(json.dumps(event) + '\n' for event in journal))

    async def read():
        response = await web.api_chat_job_stream(turn, compact=True)
        return [event async for event in response.body_iterator]

    delivered = asyncio.run(read())
    tool = json.loads(next(event['data'] for event in delivered if event['event'] == 'agent_event'))
    assert len(json.dumps(tool)) < 2500
    assert EventDetails(tmp_path).get(tool['detailRef']) == original
    stored = [json.loads(line) for line in path.read_text().splitlines()]
    assert stored[1]['data'] == original
    assert delivered[-1]['event'] == 'done'


def test_compact_snapshot_does_not_replace_the_durable_full_snapshot(tmp_path, monkeypatch):
    from web import app as web
    monkeypatch.setattr(web, '_sessions_dir', lambda: tmp_path)
    monkeypatch.setattr(web, '_turn_file', lambda _key: tmp_path / 'snapshot.json')
    web._save_turn('web:chat-one', 'done', 'ready', {'agent_events': [large_event()]})
    before = (tmp_path / 'snapshot.json').read_bytes()
    snapshot = asyncio.run(web.api_chat_last('chat-one', compact=True))
    assert len(json.dumps(snapshot)) < 3000
    assert snapshot['agent_events'][0]['detailRef']
    assert (tmp_path / 'snapshot.json').read_bytes() == before
