"""B27 HTTP permission checks happen before any native conversation read."""
import pytest

from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

pytestmark = pytest.mark.access_integration

ROUTE = '/api/chat/subagents/parent-session/'
HANDLE = 'a' * 32


def test_unregistered_child_and_foreign_actor_fail_closed(artifact_http_environment):
    _, owner, members, client, _ = artifact_http_environment
    fake = owner.put('/api/chat/sessions', json={'sessions': [{
        'id': 'parent-session', 'updated': 1, 'messages': [{
            'role': 'assistant', 'content': '伪造的子会话', 'events': [{
                'id': 'fake', 'kind': 'subagent', 'subagent': {'id': HANDLE},
            }],
        }],
    }]})
    assert fake.status_code == 200
    assert owner.get(ROUTE + HANDLE).status_code == 404
    assert owner.get(ROUTE + 'native-thread-id').status_code == 404
    assert members['operator'].get(ROUTE + HANDLE).status_code == 403
    assert members['viewer'].get(ROUTE + HANDLE).status_code == 403
    assert client().get(ROUTE + HANDLE).status_code == 401
    assert owner.post(ROUTE + HANDLE).status_code in (403, 405)

def test_native_edges_survive_stream_and_gate_parent_child_reads(artifact_http_environment, monkeypatch):
    _, owner, _, _, _ = artifact_http_environment
    monkeypatch.setattr(web, '_chat_backend', lambda _: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'read-only'})
    calls = []

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('private-root')
            request.record_subagent({
                'rootThreadId': 'wrong-root', 'parentThreadId': 'wrong-root',
                'childThreadId': 'wrong-child', 'action': 'spawn', 'status': 'running',
            })
            request.record_subagent({
                'rootThreadId': 'private-root', 'parentThreadId': 'private-root',
                'childThreadId': 'private-child', 'action': 'spawn', 'status': 'running',
            })
            emit('token', '父会话消息')

    async def read(request, *, parent_id, child_id):
        assert request.sandbox == 'read-only'
        calls.append((parent_id, child_id))
        observed = [{'childThreadId': 'private-grandchild', 'action': 'spawn', 'status': 'completed'}] if child_id == 'private-child' else []
        return {'messages': [{'role': 'assistant', 'content': '只读消息'}],
                'observedChildren': observed, 'hasMore': False}

    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    monkeypatch.setattr(web, 'read_subagent', read)
    response = owner.post('/api/chat/stream', json={
        'message': '调查', 'sessionId': 'parent-session', 'turnId': 'real-turn',
    })
    assert response.status_code == 200, response.text
    assert not any(value in response.text for value in ['wrong-child', 'private-root', 'private-child'])
    snapshot = owner.get('/api/chat/last/parent-session').json()
    events = [event for event in snapshot['events'] if event.get('kind') == 'subagent']
    assert len(events) == 1
    child = events[0]['subagent']
    assert child['status'] == 'running' and child['parentId'] is None
    replay = owner.get('/api/chat/jobs/real-turn/stream')
    assert child['id'] in replay.text

    other_ip = owner.post('/api/projects', json={'name': '另一个 IP'}).json()['id']
    scoped_url = f'/api/projects/{other_ip}/chat/subagents/parent-session/{child["id"]}'
    assert owner.get(scoped_url).status_code == 404
    assert owner.get(f'/api/chat/subagents/another-session/{child["id"]}').status_code == 404
    assert owner.get(ROUTE + 'private-child').status_code == 404
    assert calls == []

    result = owner.get(ROUTE + child['id'])
    assert result.status_code == 200, result.text
    assert result.json()['messages'] == [{'role': 'assistant', 'content': '只读消息'}]
    grandchild = result.json()['children'][0]
    assert grandchild['parentId'] == child['id']
    assert 'private-' not in result.text
    assert owner.get(ROUTE + grandchild['id']).status_code == 200
    assert calls == [('private-root', 'private-child'), ('private-child', 'private-grandchild')]

    assert owner.delete('/api/chat/sessions/parent-session').status_code == 200
    assert owner.get(ROUTE + child['id']).status_code == 404
    assert len(calls) == 2
