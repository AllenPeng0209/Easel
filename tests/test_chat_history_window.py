"""C23 turn windows preserve authoritative prefixes and unseen concurrent edits."""
import pytest

from web.chat_history import ChatHistoryStore, validated_session


def transcript(count=45):
    messages = [{'role': 'assistant', 'content': 'Legacy introduction'}]
    for index in range(count):
        messages.extend([
            {'role': 'user', 'content': f'prompt-{index}'},
            {'role': 'assistant', 'content': f'progress-{index}'},
            {'role': 'assistant', 'content': f'answer-{index}'},
        ])
    return {'id': 'session', 'messages': messages, 'updated': 1}


def test_latest_and_older_pages_never_split_turns(tmp_path):
    store = ChatHistoryStore(tmp_path)
    source = transcript()
    store.merge([source])
    tail = store.sessions(windowed=True)[0]
    assert tail['historyWindow']['start'] == 76
    assert tail['messages'] == source['messages'][76:]
    before = tail['historyWindow']['before']
    older = store.older_messages('session', before)
    assert older['historyWindow']['start'] == 16
    assert older['messages'] == source['messages'][16:76]
    oldest = store.older_messages('session', older['historyWindow']['before'])
    assert oldest['historyWindow']['start'] == 0 and oldest['historyWindow']['before'] is None
    assert oldest['historyWindow']['base']
    assert [*oldest['messages'], *older['messages'], *tail['messages']] == source['messages']
    assert store.session('session')['messages'] == source['messages']
    assert store.older_messages('unowned', before) is None


def test_partial_write_preserves_unloaded_prefix_and_rejects_newer_stale_snapshot(tmp_path):
    from web.chat_history_window import ChatHistoryWindowConflict

    store = ChatHistoryStore(tmp_path)
    source = transcript(23)
    store.merge([source])
    tail = store.sessions(windowed=True)[0]
    start = tail['historyWindow']['start']
    addition = {'role': 'user', 'content': 'new server turn'}
    fresh = store.merge([{**tail, 'updated': 2, 'messages': [*tail['messages'], addition]}], windowed=True)[0]
    assert fresh['historyWindow']['start'] == start and fresh['historyWindow']['base'] != tail['historyWindow']['base']
    assert store.session('session')['messages'] == [*source['messages'], addition]
    assert 'historyWindow' not in store.session('session')
    with pytest.raises(ChatHistoryWindowConflict):
        store.merge([{**tail, 'updated': 99, 'messages': [*tail['messages'], {'role': 'user', 'content': 'stale'}]}])
    assert store.session('session')['messages'][-1] == addition
    with pytest.raises(ChatHistoryWindowConflict):
        store.older_messages('session', tail['historyWindow']['before'])
    refreshed = store.merge([tail], windowed=True)[0]
    assert refreshed['historyWindow'] == fresh['historyWindow']


def test_current_window_cannot_truncate_loaded_suffix_and_zero_start_has_base(tmp_path):
    from web.chat_history_window import ChatHistoryWindowConflict

    store = ChatHistoryStore(tmp_path)
    source = transcript(2)
    store.merge([source])
    full = store.sessions(windowed=True)[0]
    assert full['historyWindow']['start'] == 0 and full['historyWindow']['before'] is None
    with pytest.raises(ChatHistoryWindowConflict):
        store.merge([{**full, 'updated': 2, 'messages': full['messages'][:-1]}])
    appended = {**full, 'updated': 2, 'messages': [*full['messages'], {'role': 'user', 'content': 'kept'}]}
    store.merge([appended])
    with pytest.raises(ChatHistoryWindowConflict):
        store.merge([{**full, 'updated': 5, 'title': 'cannot overwrite now'}])
    assert store.session('session')['messages'][-1]['content'] == 'kept'


def test_expanded_window_keeps_older_pages_on_save_and_new_windowless_write_cannot_shorten(tmp_path):
    from web.chat_history_window import ChatHistoryWindowConflict

    store = ChatHistoryStore(tmp_path)
    source = transcript()
    store.merge([source])
    tail = store.sessions_page(1, windowed=True)['sessions'][0]
    older = store.older_messages('session', tail['historyWindow']['before'])
    expanded = {**tail, **older, 'messages': [*older['messages'], *tail['messages']], 'title': 'Renamed', 'updated': 2}
    saved = store.merge([expanded], windowed=True)[0]
    assert saved['historyWindow']['start'] == expanded['historyWindow']['start']
    assert saved['messages'] == expanded['messages']
    assert store.session('session')['messages'] == source['messages']
    with pytest.raises(ChatHistoryWindowConflict):
        store.merge([{**source, 'messages': source['messages'][:-1], 'updated': 3}], windowed=True)
    # The compatibility endpoint's explicit complete-snapshot behavior is unchanged.
    store.merge([{**source, 'messages': source['messages'][:-1], 'updated': 3}])
    assert store.session('session')['messages'] == source['messages'][:-1]


def test_stale_base_with_no_changes_returns_current_without_advancing_revision(tmp_path):
    store = ChatHistoryStore(tmp_path)
    source = transcript()
    store.merge([source])
    tail = store.sessions(windowed=True)[0]
    changed = {**source, 'messages': [{'role': 'assistant', 'content': 'New introduction'}, *source['messages'][1:]], 'updated': 2}
    store.merge([changed])
    fresh = store.merge([{**tail, 'updated': 3}], windowed=True)[0]
    assert fresh['updated'] == 2 and fresh['messages'] == tail['messages']
    assert fresh['historyWindow']['base'] != tail['historyWindow']['base']
    assert store.session('session')['messages'][0]['content'] == 'New introduction'


def test_window_identity_validation_and_tombstones(tmp_path):
    from web.chat_history_window import ChatHistoryWindowConflict

    store = ChatHistoryStore(tmp_path)
    source = transcript()
    store.merge([source, {**source, 'id': 'other'}])
    tail = next(row for row in store.sessions(windowed=True) if row['id'] == 'session')
    assert validated_session(tail)['historyWindow'] == tail['historyWindow']
    with pytest.raises(ValueError):
        validated_session({**tail, 'historyWindow': {'start': 0, 'before': None}})
    with pytest.raises(ChatHistoryWindowConflict):
        store.older_messages('other', tail['historyWindow']['before'])
    store.delete('session')
    store.merge([{**tail, 'updated': 100}])
    assert store.is_deleted('session') and store.older_messages('session', tail['historyWindow']['before']) is None
