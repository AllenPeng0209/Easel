import pytest

from tests.test_chat_artifacts import artifact_http_environment


@pytest.mark.access_integration
def test_windowed_catalog_and_older_turns_preserve_full_authorized_export(artifact_http_environment):
    _, owner, members, anonymous, _ = artifact_http_environment
    base = '/api/chat/sessions'
    messages = [{'role': role, 'content': f'{role}-{turn}'} for turn in range(45) for role in ('user', 'assistant')]
    data = {'id': 'history-test', 'title': 'Long chat', 'updated': 10, 'messages': messages}
    result = owner.put(base + '?windowed=true', json={'sessions': [data]})
    assert result.status_code == 200, result.text
    first = owner.get(base + '?limit=50&windowed=true').json()['sessions'][0]
    assert first['messages'] == messages[50:]
    history = base + '/history-test/history'
    assert anonymous().get(history + '?full=true').status_code == 401
    assert members['viewer'].get(history + '?full=true').status_code == 403
    assert members['operator'].get(history + '?full=true').status_code == 404
    assert owner.get(history + '?full=true').json()['messages'] == messages
    cursor = first['historyWindow']['before']
    older = owner.get(history, params={'before': cursor}).json()
    assert older['messages'] == messages[10:50]
    updated = {**first, 'updated': 11, 'messages': [*first['messages'], {'role': 'user', 'content': 'appended'}]}
    result = owner.put(base + '?windowed=true', json={'sessions': [updated]})
    assert result.status_code == 200, result.text
    assert len(owner.get(history + '?full=true').json()['messages']) == 91
    assert owner.get(history, params={'before': cursor}).status_code == 409
    assert owner.put(base + '?windowed=true', json={'sessions': [{**first, 'updated': 12, 'title': 'stale'}]}).status_code == 409
