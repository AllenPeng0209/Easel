"""C32 verified context measurements survive the normal chat recovery surfaces."""
import json

import pytest

import web.app as web
from easel.harnesses.events import EventProjector
from tests.test_agent_harnesses import sandbox


def usage_event(text):
    return next(json.loads(line.removeprefix('data: ')) for line in text.splitlines()
                if line.startswith('data: ') and '"kind": "usage"' in line)


@pytest.mark.parametrize('runtime,expected', [
    ('codex', {'source': 'codex', 'usedTokens': 45, 'capacityTokens': 100}),
    ('opencode', {'source': 'acp', 'usedTokens': 45, 'capacityTokens': 100}),
])
def test_native_context_measurements_replay_and_persist_in_catalog(sandbox, monkeypatch, runtime, expected):
    monkeypatch.setattr(web, '_chat_backend', lambda _: runtime)

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('context-fixture')
            projector = EventProjector(emit, {})
            if runtime == 'codex':
                projector.codex('thread/tokenUsage/updated', {'tokenUsage': {
                    'last': {'totalTokens': 45}, 'total': {'totalTokens': 9000}, 'modelContextWindow': 100,
                }})
            else:
                projector.acp({'sessionUpdate': 'usage_update', 'used': 45, 'size': 100})
            emit('token', 'answer')

    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    response = sandbox.post('/api/chat/stream', json={'message': 'measure', 'sessionId': 'context-chat',
                                                     'turnId': 'context-turn'})
    assert response.status_code == 200, response.text
    event = usage_event(response.text)
    assert event['contextUsage'] == expected
    assert usage_event(sandbox.get('/api/chat/jobs/context-turn/stream').text) == event
    snapshot = sandbox.get('/api/chat/last/context-chat').json()
    assert snapshot['events'] == [event]
    saved = sandbox.put('/api/chat/sessions?windowed=true', json={'sessions': [
        {'id': 'context-chat', 'updated': 1, 'messages': [{'role': 'assistant', 'content': 'answer',
                                                           'events': snapshot['events']}]},
    ]})
    assert saved.status_code == 200, saved.text
    catalog = sandbox.get('/api/chat/sessions?windowed=true').json()
    assert catalog['sessions'][0]['messages'][0]['events'] == [event]
