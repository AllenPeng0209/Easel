"""Server conversation catalog must merge, retain and isolate browser histories."""
import pytest


def entry(identifier, updated, text='hello'):
    return {'id': identifier, 'title': identifier, 'messages': [{'role': 'user', 'content': text}],
            'created': 1, 'updated': updated}


def test_catalog_retains_more_than_one_hundred_and_reloads(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    store.merge([entry(str(i), i + 1) for i in range(125)])
    assert len(ChatHistoryStore(tmp_path).sessions()) == 125
    assert store.sessions()[0]['id'] == '124'


def test_catalog_pages_by_id_through_recency_changes_and_cursor_deletion(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    store.merge([entry('d', 3), entry('b', 8), entry('a', 1),
                 {**entry('c', 5), 'archived': True}])
    store.delete('z')

    first = store.sessions_page(2)
    assert [row['id'] for row in first['sessions']] == ['a', 'b']
    assert first['nextCursor'] == 'b'
    assert first['deletedIds'] == []

    store.merge([entry('a', 100), entry('d', 101)])
    store.delete('b')
    second = store.sessions_page(2, first['nextCursor'])
    assert [row['id'] for row in second['sessions']] == ['c', 'd']
    assert second['sessions'][0]['archived'] is True
    assert second['nextCursor'] is None
    assert second['deletedIds'] == ['b', 'z']
    assert [row['id'] for row in store.sessions()] == ['d', 'a', 'c']


def test_stale_browser_cannot_drop_newer_messages_or_other_sessions(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    store.merge([entry('a', 2, 'new'), entry('b', 3)])
    store.merge([entry('a', 1, 'old')])
    assert {row['id']: row['messages'][0]['content'] for row in store.sessions()} == {'b': 'hello', 'a': 'new'}


def test_deleted_catalog_entry_does_not_reappear_from_stale_snapshot(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    store.merge([entry('a', 2)])
    store.delete('a')
    store.merge([entry('a', 3)])
    assert store.sessions() == []


def test_user_ip_directories_are_independent(tmp_path):
    from web.chat_history import ChatHistoryStore
    first = ChatHistoryStore(tmp_path / 'user-1' / 'ip-a')
    second = ChatHistoryStore(tmp_path / 'user-2' / 'ip-a')
    third = ChatHistoryStore(tmp_path / 'user-1' / 'ip-b')
    first.merge([entry('same', 1, 'private')])
    assert second.sessions() == []
    assert third.sessions() == []


@pytest.mark.parametrize('bad', [
    {'id': '../bad', 'messages': 'wrong'},
    {'id': 'bad', 'messages': [{'role': 'system', 'content': 'fake'}]},
    {'id': 'bad', 'messages': [], 'updated': float('nan')},
    {'id': 'bad', 'messages': [], 'updated': True},
])
def test_invalid_catalog_data_never_commits(tmp_path, bad):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.merge([entry('good', 1), bad])
    assert store.sessions() == []


@pytest.mark.access_integration
def test_catalog_http_boundary_is_private_to_user_and_ip(tmp_path, monkeypatch):
    # These legacy alias checks represent a migrated, non-empty default IP.
    (tmp_path / 'outputs' / 'legacy-topic').mkdir(parents=True)
    from fastapi.testclient import TestClient
    from web import app as web
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'outputs' / '_sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')

    def client():
        return TestClient(web.app, base_url='http://localhost:7860',
                          headers={'Origin': 'http://localhost:7860'}, client=('127.0.0.1', 50000))
    owner = client()
    assert owner.post('/api/auth/setup', json={'username': 'owner', 'password': 'catalog-owner-tests'}).status_code == 200
    created = owner.post('/api/users', json={'username': 'operator', 'password': 'catalog-operator-tests'}).json()
    assert owner.post('/api/projects/default/members', json={'user_id': created['id'], 'role': 'operator'}).status_code == 200
    operator = client()
    assert operator.post('/api/auth/login', json={'username': 'operator', 'password': 'catalog-operator-tests'}).status_code == 200
    assert client().get('/api/chat/sessions').status_code == 401
    own = owner.put('/api/chat/sessions', json={'sessions': [entry('same', 1, 'owner-private')]})
    assert own.status_code == 200, own.text
    assert owner.get('/api/chat/sessions?limit=1').json() == {
        'sessions': own.json()['sessions'], 'deletedIds': [], 'nextCursor': None,
    }
    for query in ('limit=0', 'limit=101', 'limit=invalid', 'cursor=same', 'limit=1&cursor='):
        assert owner.get('/api/chat/sessions?' + query).status_code == 422
    assert operator.get('/api/chat/sessions').json() == {'sessions': [], 'deletedIds': []}
    assert operator.get('/api/chat/sessions?limit=1').json() == {'sessions': [], 'deletedIds': [], 'nextCursor': None}
    assert operator.put('/api/chat/sessions', json={'sessions': [entry('same', 1, 'operator-private')]}).status_code == 200
    assert owner.get('/api/chat/sessions').json()['sessions'][0]['messages'][0]['content'] == 'owner-private'
    other_ip = owner.post('/api/projects', json={'name': 'second-ip'}).json()['id']
    assert owner.get(f'/api/projects/{other_ip}/chat/sessions').json() == {'sessions': [], 'deletedIds': []}
    assert owner.get(f'/api/projects/{other_ip}/chat/sessions?limit=1').json() == {
        'sessions': [], 'deletedIds': [], 'nextCursor': None,
    }
    assert operator.get(f'/api/projects/{other_ip}/chat/sessions').status_code == 403
    assert owner.delete('/api/chat/sessions/same').json()['nativeHistoryDeleted'] is False
    assert owner.get('/api/chat/sessions').json() == {'sessions': [], 'deletedIds': ['same']}
    assert operator.get('/api/chat/sessions').json()['sessions'][0]['messages'][0]['content'] == 'operator-private'


def test_catalog_preserves_queued_work_and_pause_state(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    value = {**entry('a', 1), 'queued': [{'id': 'next', 'text': 'next task', 'created': 1}], 'queuePaused': True}
    store.merge([value])
    assert store.sessions()[0]['queued'] == [{'id': 'next', 'text': 'next task', 'created': 1}]
    assert store.sessions()[0]['queuePaused'] is True


def test_catalog_round_trips_delivery_state_and_legacy_queue_rows(tmp_path):
    from web.chat_history import ChatHistoryStore
    queue = [{'id': 'legacy', 'text': 'legacy request'}]
    queue.extend({'id': state, 'text': state, 'delivery': state}
                 for state in ('sending', 'rejected', 'unconfirmed'))
    ChatHistoryStore(tmp_path).merge([{**entry('a', 1), 'queued': queue}])
    assert ChatHistoryStore(tmp_path).sessions()[0]['queued'] == queue


@pytest.mark.parametrize('state', ['admitted', 'pending', '', False, None, []])
def test_invalid_queue_delivery_state_rejects_the_catalog_batch(tmp_path, state):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    value = {**entry('a', 1), 'queued': [{'id': 'next', 'text': 'work', 'delivery': state}]}
    with pytest.raises(ValueError):
        store.merge([entry('valid', 1), value])
    assert store.sessions() == []


def test_missing_historical_attachment_does_not_prevent_catalog_sync(tmp_path, monkeypatch):
    import asyncio
    from web import app as web
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    scope = web._attachment_scope('saved')
    path = f'_inbox/{scope}/batch/file.txt'
    value = entry('saved', 1)
    value['messages'][0]['attachments'] = [{'id': web._attachment_id(scope, path), 'name': 'file.txt', 'path': path}]
    result = asyncio.run(web.api_chat_sessions_merge(web.ChatSessionsRequest(sessions=[value])))
    assert result['sessions'][0]['messages'][0]['attachments'][0]['name'] == 'file.txt'


def test_catalog_rejects_foreign_session_attachment_even_when_file_missing(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from web import app as web
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    scope = web._attachment_scope('foreign')
    path = f'_inbox/{scope}/batch/file.txt'
    value = entry('mine', 1)
    value['messages'][0]['attachments'] = [{'id': web._attachment_id(scope, path), 'name': 'file.txt', 'path': path}]
    with pytest.raises(HTTPException) as error:
        asyncio.run(web.api_chat_sessions_merge(web.ChatSessionsRequest(sessions=[value])))
    assert error.value.status_code == 403


def test_malformed_queued_request_is_not_persisted(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    value = {**entry('a', 1), 'queued': [{'id': 'next', 'text': None}]}
    with pytest.raises(ValueError):
        store.merge([value])
    assert store.sessions() == []


def test_concurrent_turn_claims_have_one_winner_and_preserve_event_log(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from web.chat_history import ChatHistoryStore
    path = tmp_path / 'jobs' / 'one.jsonl'
    def claim(_):
        return ChatHistoryStore(tmp_path).claim_turn('one', 'session', 'hash', path)[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(8)))
    assert results.count(True) == 1
    path.write_text('original-result')
    assert ChatHistoryStore(tmp_path).claim_turn('one', 'session', 'hash', path)[0] is False
    assert path.read_text() == 'original-result'


def test_deleted_ids_are_available_after_store_reload(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    store.merge([entry('a', 1), entry('b', 2)])
    store.delete('a')
    reloaded = ChatHistoryStore(tmp_path)
    assert reloaded.deleted_ids() == ['a']
    assert [item['id'] for item in reloaded.sessions()] == ['b']


def test_catalog_unread_completion_round_trips_and_obeys_snapshot_order(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    store.merge([entry('legacy', 1), {**entry('completed', 3), 'unreadCompletion': True}])
    reopened = ChatHistoryStore(tmp_path)
    sessions = {session['id']: session for session in reopened.sessions()}
    assert 'unreadCompletion' not in sessions['legacy']
    assert sessions['completed']['unreadCompletion'] is True
    reopened.merge([{**entry('completed', 4), 'unreadCompletion': False}])
    reopened.merge([{**entry('completed', 3), 'unreadCompletion': True}])
    assert reopened.sessions()[0]['unreadCompletion'] is False


@pytest.mark.parametrize('invalid', [None, 0, 1, 'false', []])
def test_catalog_rejects_nonboolean_unread_completion_without_partial_merge(tmp_path, invalid):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    with pytest.raises(ValueError):
        store.merge([entry('good', 1), {**entry('bad', 2), 'unreadCompletion': invalid}])
    assert store.sessions() == []
