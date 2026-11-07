"""Compact transport must retain the exact durable tool record and its scope."""
import copy
import json

import pytest

pytestmark = pytest.mark.access_integration


def large_event():
    return {'id': 'event-one', 'kind': 'tool', 'runId': 'run-one', 'callId': 'call-one',
            'name': 'bash', 'status': 'completed', 'outputStream': 'merged', 'exitCode': 0,
            'input': {'command': 'inspect original video'}, 'output': 'complete diagnostic output\n' * 20000}


def test_compact_events_restore_the_exact_original_and_do_not_mutate_the_input(tmp_path):
    from web.chat_event_details import EventDetails
    original = large_event()
    before = copy.deepcopy(original)
    store = EventDetails(tmp_path)
    compact = store.compact_event(original)
    assert len(json.dumps(compact)) < 2500
    assert compact['callId'] == 'call-one'
    assert compact['status'] == 'completed'
    assert compact['exitCode'] == 0
    assert compact['detailRef']
    assert store.restore_event(compact) == original
    assert original == before
    assert store.get(compact['detailRef']) == original


def test_event_references_are_private_to_the_session_directory_and_bound_to_identity(tmp_path):
    from web.chat_event_details import EventDetails
    first = EventDetails(tmp_path / 'user-a')
    second = EventDetails(tmp_path / 'user-b')
    compact = first.compact_event(large_event())
    with pytest.raises((FileNotFoundError, ValueError)):
        second.get(compact['detailRef'])
    with pytest.raises(ValueError):
        first.get('../another-user')
    with pytest.raises(ValueError):
        first.restore_event({**compact, 'callId': 'different-call'})


def test_typed_host_business_receipts_remain_available_in_the_compact_view(tmp_path):
    from web.chat_event_details import EventDetails
    event = {'id': 'business-one', 'kind': 'business', 'name': 'easel_business_objects',
             'business': {'id': 'workshop-one', 'kind': 'workshop', 'summary': 'ready', 'details': {'proof': 'x' * 4000}}}
    assert EventDetails(tmp_path).compact_event(event) == event


def test_delta_merge_reads_back_only_the_submitted_sessions(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    store.merge([{'id': 'first', 'created': 1, 'updated': 1, 'messages': []},
                 {'id': 'second', 'created': 1, 'updated': 1, 'messages': []}])
    result = store.merge([{'id': 'first', 'created': 1, 'updated': 2, 'title': 'renamed', 'messages': []}],
                         only_submitted=True)
    assert [session['id'] for session in result] == ['first']
    assert store.session('second') is not None


def test_recent_catalog_page_keeps_older_history_reachable_after_cursor_deletion(tmp_path):
    from web.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path)
    store.merge([{'id': name, 'created': 1, 'updated': updated, 'messages': []}
                 for name, updated in [('old', 1), ('middle', 2), ('new', 3)]])
    first = store.recent_page(2, windowed=True)
    assert [item['id'] for item in first['sessions']] == ['new', 'middle']
    store.delete('middle')
    following = store.recent_page(2, first['nextCursor'], windowed=True)
    assert [item['id'] for item in following['sessions']] == ['old']
    assert following['nextCursor'] is None
    assert 'middle' in following['deletedIds']


@pytest.fixture
def catalog_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from easel.access import AccessStore
    from web import app as web
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'outputs' / '_sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    _, token = AccessStore(tmp_path).setup('owner', 'event-details-tests-42', project_ids=('default',))
    client = TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 50000),
                        headers={'Origin': 'http://localhost:7860', 'Cookie': 'easel_session=' + token})
    session = {'id': 'chat-one', 'title': 'Original transcript', 'created': 1, 'updated': 2,
               'messages': [{'role': 'assistant', 'content': 'Video ready', 'events': [large_event()]}]}
    assert client.put('/api/chat/sessions', json={'sessions': [session]}).status_code == 200
    return client, session


def test_catalog_compact_round_trip_preserves_full_history_and_protects_detail_fetch(catalog_client):
    client, original = catalog_client
    result = client.get('/api/chat/sessions?limit=50&windowed=true&compact=true')
    assert result.status_code == 200
    assert len(result.content) < 5000
    projected = result.json()['sessions'][0]
    event = projected['messages'][0]['events'][0]
    detail_url = '/api/chat/event-details/' + event['detailRef']
    assert client.get(detail_url).json() == large_event()
    projected['title'] = 'Renamed without re-uploading the tool output'
    projected['updated'] = 3
    saved = client.put('/api/chat/sessions?windowed=true&compact=true', json={'sessions': [projected]})
    assert saved.status_code == 200, saved.text
    assert len(saved.content) < 5000
    complete = client.get('/api/chat/sessions/chat-one/history?full=true').json()
    assert complete['messages'] == original['messages']
    assert complete['title'] == projected['title']
    client.headers.pop('Cookie')
    assert client.get(detail_url).status_code == 401
