"""C15 actual scoped fork context reaches the model without cloning source authority."""
import hashlib
import json

import pytest

from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

pytestmark = pytest.mark.access_integration


def test_child_run_receives_only_frozen_visible_prefix(artifact_http_environment, monkeypatch):
    _, owner, _, _, _ = artifact_http_environment
    calls = []

    async def model(_settings, content, **_options):
        calls.append(content)
        yield 'token', '已生成答复'
        yield 'finish', 'stop'

    monkeypatch.setattr(web, '_chat_backend', lambda _: 'api')
    monkeypatch.setattr(web, 'stream_chat', model)
    request = {'message': '第一轮任务', 'turnId': 'source-finished', 'sessionId': 'source-chat'}
    original = owner.post('/api/chat/stream', json=request)
    assert original.status_code == 200 and 'event: done' in original.text
    prefix = [{'role': 'user', 'content': request['message']},
              {'role': 'assistant', 'content': '已生成答复', 'status': 'completed', 'clientTurnId': request['turnId']}]
    source = {'id': 'source-chat', 'title': '原对话', 'updated': 10, 'messages': [
        *prefix, {'role': 'user', 'content': '截止点之后不能继承'},
    ]}
    assert owner.put('/api/chat/sessions', json={'sessions': [source]}).status_code == 200
    digest = hashlib.sha256(json.dumps([[item['role'], item['content']] for item in prefix],
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    response = owner.post('/api/chat/sessions/source-chat/fork', json={
        'cutoffIndex': 1, 'cutoffTurnId': request['turnId'], 'prefixHash': digest, 'requestId': 'fork-test',
    })
    assert response.status_code == 200, response.text
    child = response.json()['session']
    assert child['id'] != source['id'] and len(child['messages']) == 2 and len(calls) == 1
    request = {'message': '在新分支继续', 'sessionId': child['id'], 'turnId': 'child-first'}
    first = owner.post('/api/chat/stream', json=request)
    assert first.status_code == 200 and 'event: done' in first.text
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert len(calls) == 2
    assert '<fork_context>' in str(calls[-1]) and '第一轮任务' in str(calls[-1])
    assert '截止点之后不能继承' not in str(calls[-1])
