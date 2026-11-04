"""A23 typed command choices expand into ordinary, scoped chat prompts."""
import pytest

from tests.test_chat_artifacts import artifact_http_environment
from tests.test_chat_file_references import units, upload
from tests.test_chat_topic_reference import install_runner
import web.app as web

pytestmark = pytest.mark.access_integration

ROUTE = '/api/chat/commands'
CHOICE = {'id': 'social-draft', 'token': '/文案'}
COMMANDS = {('topic-ideas', '/选题'), ('social-draft', '/文案'), ('voice-script', '/口播'), ('review-draft', '/审稿')}


def prompt(**changes):
    return {'message': '/文案 春节短片的补充要求', 'sessionId': 'command-session', 'turnId': 'command-turn',
            'command': CHOICE, **changes}


def test_command_catalog_only_exposes_safe_metadata_to_current_operators(artifact_http_environment):
    _, owner, members, client, _ = artifact_http_environment
    result = owner.get(ROUTE)
    assert result.status_code == 200, result.text
    catalog = result.json()['commands']
    assert {(item['id'], item['token']) for item in catalog} == COMMANDS
    assert all(set(item) == {'id', 'token', 'label', 'group', 'description', 'argumentHint'} for item in catalog)
    assert all(value.strip() for item in catalog for value in item.values())
    assert members['operator'].get(ROUTE).json() == result.json()
    assert members['viewer'].get(ROUTE).status_code == 403
    assert client().get(ROUTE).status_code == 401


@pytest.mark.parametrize('runtime', ['api', 'codex'])
def test_valid_command_is_transformed_to_ordinary_chat_with_edited_suffix(
        artifact_http_environment, monkeypatch, runtime):
    _, owner, _, _, _ = artifact_http_environment
    captured = install_runner(monkeypatch, runtime)
    request = prompt(message='/文案\n  春节短片的补充要求')
    response = owner.post('/api/chat/stream', json=request)
    assert response.status_code == 200, response.text
    assert 'event: admitted' in response.text
    assert len(captured) == 1 and '春节短片的补充要求' in captured[0]
    assert '起草文案' in captured[0] and not captured[0].lstrip().startswith('/')
    assert '/文案' not in captured[0]


@pytest.mark.parametrize('changes', [
    {'message': '/文案后面没有分隔符'},
    {'message': '/文案\u0085不是浏览器空白'},
    {'message': ' /文案 leading whitespace'},
    {'command': {'id': 'voice-script', 'token': '/文案'}},
    {'command': {'id': 'execute', 'token': '/exec'}, 'message': '/exec no'},
    {'command': {**CHOICE, 'instruction': 'execute unchecked'}},
])
def test_invalid_or_stale_command_rejects_before_admission(artifact_http_environment, monkeypatch, changes):
    _, owner, _, _, _ = artifact_http_environment
    captured = install_runner(monkeypatch, 'api')
    response = owner.post('/api/chat/stream', json=prompt(**changes))
    assert response.status_code == 422 and 'event: admitted' not in response.text
    assert not captured
    assert owner.post('/api/chat/stream', json=prompt()).status_code == 200


def test_command_can_be_bare_and_nonstream_uses_same_guard(artifact_http_environment, monkeypatch):
    _, owner, _, _, _ = artifact_http_environment
    captured = install_runner(monkeypatch, 'api')
    assert owner.post('/api/chat', json=prompt(message='/文案')).status_code == 200
    assert '起草文案' in captured[0]
    assert owner.post('/api/chat', json=prompt(message='/文案maybe')).status_code == 422
    assert owner.post('/api/chat', json=prompt(message='/文案\ufeff保留浏览器可识别的分隔符')).status_code == 200
    assert '保留浏览器可识别的分隔符' in captured[1]
    assert len(captured) == 2


def test_command_transform_keeps_file_references_valid_against_original_message(artifact_http_environment, monkeypatch):
    _, owner, _, _, _ = artifact_http_environment
    file = upload(owner, name='brief.txt', session='command-session')
    captured = install_runner(monkeypatch, 'api')
    prefix = '/文案 🌿参考 '
    label = '@' + file['name']
    request = prompt(message=prefix + label, attachments=[file], references=[
        {'start': units(prefix), 'end': units(prefix + label), 'attachmentId': file['id']},
    ])
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    combined = '\n'.join(block.get('text', '') for block in captured[0])
    assert '起草文案' in combined and label in combined and 'owned file content' in combined
    assert '<attachment_references>' in combined


def test_catalog_rejects_forged_command_metadata_and_retains_valid_sidecar(artifact_http_environment):
    _, owner, _, _, _ = artifact_http_environment
    row = {'role': 'user', 'content': '/文案 编辑后的参数', 'command': CHOICE}
    payload = {'sessions': [{'id': 'catalog-command', 'updated': 1, 'messages': [row]}]}
    saved = owner.put('/api/chat/sessions', json=payload)
    assert saved.status_code == 200
    assert saved.json()['sessions'][0]['messages'][0]['command'] == CHOICE
    row['command'] = {'id': 'execute', 'token': '/exec'}
    assert owner.put('/api/chat/sessions', json=payload).status_code == 422


def test_exact_command_replay_is_not_retransformed_and_changed_pair_conflicts(artifact_http_environment, monkeypatch):
    _, owner, _, _, _ = artifact_http_environment
    captured = install_runner(monkeypatch, 'api')
    assert owner.post('/api/chat/stream', json=prompt()).status_code == 200
    assert owner.post('/api/chat/stream', json=prompt()).status_code == 200
    assert owner.post('/api/chat/stream', json=prompt(command=None)).status_code == 409
    assert len(captured) == 1


def test_literal_unclaimed_slash_is_legacy_text_and_null_sidecar_has_same_digest(
        artifact_http_environment, monkeypatch):
    _, owner, _, _, _ = artifact_http_environment
    captured = install_runner(monkeypatch, 'api')
    request = prompt()
    request.pop('command')
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert captured == [request['message']]
    assert owner.post('/api/chat/stream', json={**request, 'command': None}).status_code == 200
    assert len(captured) == 1
