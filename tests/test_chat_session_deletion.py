"""C14 deletion retires only the catalog, never existing work or evidence."""
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_chat_artifacts import artifact_http_environment
from tests.test_chat_history import entry
from tests.test_chat_topic_reference import install_runner
from web.chat_history import ChatHistoryStore
import web.app as web


def test_catalog_delete_clears_payload_and_keeps_previous_admission_only(tmp_path):
    store = ChatHistoryStore(tmp_path)
    old = tmp_path / 'old.jsonl'
    assert store.claim_turn('old', 'same', 'existing-hash', old)[0]
    old.write_text('historic evidence')
    store.merge([entry('same', 2, 'private original text')])
    store.delete('same')
    store.merge([entry('same', 3, 'stale client text')])
    with sqlite3.connect(store.path) as database:
        assert database.execute('SELECT deleted,payload FROM sessions WHERE id=?', ('same',)).fetchone() == (1, '{}')
    with pytest.raises(ValueError):
        store.claim_turn('new', 'same', 'new-hash', tmp_path / 'new.jsonl')
    assert store.turn_claim('new') is None and not (tmp_path / 'new.jsonl').exists()
    assert not store.claim_turn('old', 'same', 'existing-hash', old)[0]
    assert old.read_text() == 'historic evidence'


@pytest.mark.access_integration
def test_delete_receipt_is_exact_and_old_turn_replay_survives(artifact_http_environment, monkeypatch):
    root, owner, members, _, _ = artifact_http_environment
    prompts = install_runner(monkeypatch, 'api')
    body = {'message': 'Create a useful draft', 'sessionId': 'retired', 'turnId': 'historic'}
    assert owner.put('/api/chat/sessions', json={'sessions': [entry('retired', 1)]}).status_code == 200
    assert owner.post('/api/chat/stream', json=body).status_code == 200
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        journal = web._job_event_file('historic')
        before = journal.read_bytes()
        result_file = web._turn_file('web:retired')
        result_file.write_text('{"status":"done","text":"historical result"}')
        direct_history = web.history_path(web._sessions_dir(), 'retired')
        direct_history.parent.mkdir(parents=True, exist_ok=True)
        direct_history.write_text('[{"role":"assistant","content":"native history"}]')
    removed = owner.delete('/api/chat/sessions/retired')
    assert removed.status_code == 200 and removed.json() == {
        'ok': True, 'sessionId': 'retired', 'catalogDeleted': True, 'nativeHistoryDeleted': False,
    }
    assert owner.delete('/api/chat/sessions/retired').json() == removed.json()
    assert owner.post('/api/chat/stream', json={**body, 'turnId': 'fresh'}).status_code == 410
    assert owner.post('/api/chat', json={'sessionId': 'retired', 'message': 'stale'}).status_code == 410
    replay = owner.post('/api/chat/stream', json=body)
    assert replay.status_code == 200 and 'event: admitted' in replay.text and 'completed' in replay.text
    assert journal.read_bytes() == before and len(prompts) == 1
    assert 'historical result' in result_file.read_text() and 'native history' in direct_history.read_text()
    assert (root / 'outputs' / 'topic' / 'report.txt').read_bytes() == b'owned-result'
    assert owner.get('/api/chat/sessions').json() == {'sessions': [], 'deletedIds': ['retired']}
    assert members['operator'].get('/api/chat/sessions').json() == {'sessions': [], 'deletedIds': []}


@pytest.mark.access_integration
def test_delete_wins_when_new_turn_is_waiting_on_preflight(artifact_http_environment, monkeypatch):
    root, owner, _, _, _ = artifact_http_environment
    prompts = install_runner(monkeypatch, 'api')
    entered, release = Event(), Event()

    async def preflight(_):
        entered.set()
        assert await asyncio.to_thread(release.wait, 3)
        return None, None

    monkeypatch.setattr(web, '_resolve_chat_references', preflight)
    body = {'sessionId': 'racing', 'turnId': 'not-admitted', 'message': 'Stale prompt'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(owner.post, '/api/chat/stream', json=body)
        try:
            assert entered.wait(2)
            assert owner.delete('/api/chat/sessions/racing').status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 410
    assert not prompts
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        assert not web._job_event_file('not-admitted').exists()


@pytest.mark.access_integration
def test_delete_storage_failure_returns_no_receipt_and_keeps_catalog(artifact_http_environment, monkeypatch):
    _, owner, _, _, _ = artifact_http_environment
    assert owner.put('/api/chat/sessions', json={'sessions': [entry('present', 1)]}).status_code == 200

    def fail(*_):
        raise sqlite3.OperationalError('private storage failure')

    monkeypatch.setattr(ChatHistoryStore, 'delete', fail)
    response = owner.delete('/api/chat/sessions/present')
    assert response.status_code == 503 and 'private' not in response.text
    catalog = owner.get('/api/chat/sessions').json()
    assert [item['id'] for item in catalog['sessions']] == ['present'] and catalog['deletedIds'] == []
