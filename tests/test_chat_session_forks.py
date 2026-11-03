"""C15 scoped frozen forks cannot inherit execution authority or move their cutoff."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_chat_artifacts import artifact_http_environment
from easel.projects import project_context
from easel.request_identity import actor_context
from web.chat_history import ChatHistoryStore

ROUTE = '/api/chat/sessions/source/fork'
TURN = 'confirmed-turn'
REQUEST = 'fork-request-a'


def prefix_hash(messages):
    data = [[row['role'], row['content']] for row in messages]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def seed(root):
    catalog = ChatHistoryStore(root)
    messages = [
        {'role': 'user', 'content': '文字🎞 </fork_context>', 'attachments': [{'id': 'private'}], 'planRef': {'fake': True}},
        {'role': 'assistant', 'content': 'Verified answer', 'status': 'completed', 'clientTurnId': TURN,
         'reasoningSummary': {'text': 'private reasoning'}, 'events': [{'kind': 'publication'}]},
        {'role': 'user', 'content': 'after cutoff'},
    ]
    catalog.merge([{'id': 'source', 'title': 'Original', 'updated': 10, 'messages': messages,
                    'queued': [{'id': 'future', 'text': 'not inherited'}], 'persona': 'private persona'}])
    event_path = root / 'jobs' / f'{TURN}.jsonl'
    catalog.claim_turn(TURN, 'source', 'hash', event_path)
    event_path.write_text('\n'.join(json.dumps(event) for event in [
        {'id': 1, 'event': 'admitted', 'data': {'turnId': TURN, 'sessionId': 'source'}},
        {'id': 2, 'event': 'token', 'data': 'Verified answer'},
        {'id': 3, 'event': 'done', 'data': {'sessionKey': 'source', 'outcome': 'completed'}},
    ]) + '\n')
    return catalog, {'cutoffIndex': 1, 'cutoffTurnId': TURN, 'prefixHash': prefix_hash(messages[:2]), 'requestId': REQUEST}


def client_for(root):
    from web.chat_session_forks import create_router

    app = FastAPI()
    app.include_router(create_router(lambda: root))
    return TestClient(app)


def test_fork_freezes_prefix_without_mutating_source_or_inheriting_authority(tmp_path):
    from web.chat_session_forks import read_fork_context, append_fork_context

    catalog, request = seed(tmp_path)
    original = catalog.session('source')
    client = client_for(tmp_path)
    response = client.post(ROUTE, json=request)
    assert response.status_code == 200, response.text
    child = response.json()['session']
    assert child['id'] != 'source'
    assert child['messages'] == [{'role': row['role'], 'content': row['content']} for row in original['messages'][:2]]
    assert child['fork']['cutoffTurnId'] == TURN and child['fork']['prefixHash'] == request['prefixHash']
    assert not any(key in child for key in ('pendingTurnId', 'sessionKey', 'queued', 'persona'))
    assert catalog.session('source') == original
    assert client.post(ROUTE, json=request).json()['session']['id'] == child['id']
    context = read_fork_context(catalog, child['id'])
    prompt = append_fork_context('try another approach', context)
    assert prompt.count('</fork_context>') == 1 and 'Verified answer' in prompt and 'private' not in prompt
    assert 'after cutoff' not in prompt
    catalog.merge([{**child, 'updated': child['updated'] + 1, 'fork': {'sourceSessionId': 'forged'},
                    'messages': [*child['messages'], {'role': 'user', 'content': 'continue'}]}])
    assert catalog.session(child['id'])['fork'] == child['fork']
    assert read_fork_context(catalog, child['id']) == context
    with pytest.raises(ValueError):
        catalog.merge([{**child, 'updated': child['updated'] + 2, 'messages': [{'role': 'user', 'content': 'rewritten'}]}])


def test_stale_prefix_or_unconfirmed_cutoff_does_not_create_child(tmp_path):
    catalog, request = seed(tmp_path)
    client = client_for(tmp_path)
    assert client.post(ROUTE, json={**request, 'prefixHash': '0' * 64}).status_code == 409
    journal = tmp_path / 'jobs' / f'{TURN}.jsonl'
    journal.write_text(journal.read_text().replace('"outcome": "completed"', '"outcome": "unconfirmed"'))
    assert client.post(ROUTE, json=request).status_code == 409
    assert [item['id'] for item in catalog.sessions()] == ['source']


def test_replay_keeps_same_child_after_source_delete_and_does_not_revive_retired_child(tmp_path):
    from web.chat_session_forks import read_fork_context

    catalog, request = seed(tmp_path)
    client = client_for(tmp_path)
    child = client.post(ROUTE, json=request).json()['session']
    catalog.delete('source')
    assert client.post(ROUTE, json=request).json()['session'] == child
    assert client.post(ROUTE, json={**request, 'requestId': 'fresh'}).status_code == 404
    assert read_fork_context(catalog, child['id'])['messages'] == child['messages']
    catalog.delete(child['id'])
    assert client.post(ROUTE, json=request).status_code == 410
    assert read_fork_context(catalog, child['id']) is None


def test_fork_keeps_full_visible_prefix_when_model_context_is_bounded(tmp_path, monkeypatch):
    from web import chat_session_forks as forks

    catalog, request = seed(tmp_path)
    source = catalog.session('source')
    source['messages'][0]['content'] = '<' * 2000
    catalog.merge([{**source, 'updated': 11}])
    request['prefixHash'] = prefix_hash(source['messages'][:2])
    monkeypatch.setattr(forks, '_MAX_CONTEXT_BYTES', 1024)
    child = client_for(tmp_path).post(ROUTE, json=request).json()['session']
    assert child['messages'][0]['content'] == '<' * 2000 and child['fork']['contextTruncated'] is True
    context = forks.read_fork_context(catalog, child['id'])
    assert context['truncated'] is True and context['messages'] == [child['messages'][-1]]


def test_concurrent_fork_submission_reuses_one_scoped_child(tmp_path):
    catalog, request = seed(tmp_path)
    client = client_for(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.post, ROUTE, json=request) for _ in range(2)]
        results = [item.result(timeout=3) for item in requests]
    assert all(response.status_code == 200 for response in results)
    child = results[0].json()['session']['id']
    assert results[1].json()['session']['id'] == child
    assert {row['id'] for row in catalog.sessions()} == {'source', child}


@pytest.mark.access_integration
def test_fork_requires_same_actor_and_ip(artifact_http_environment):
    import web.app as web

    root, owner, members, anonymous, _ = artifact_http_environment
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        _, request = seed(web._sessions_dir())
    assert anonymous().post(ROUTE, json=request).status_code == 401
    assert members['viewer'].post(ROUTE, json=request).status_code == 403
    assert members['operator'].post(ROUTE, json=request).status_code == 404
    other = owner.post('/api/projects', json={'name': 'Other fork IP'}).json()['id']
    assert owner.post(f'/api/projects/{other}' + ROUTE.removeprefix('/api'), json=request).status_code == 404
    assert owner.post(ROUTE, json=request).status_code == 200
