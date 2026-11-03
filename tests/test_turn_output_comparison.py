"""D07 a round's text comparisons are frozen, scoped and never reopen source paths."""
import hashlib
import json

import pytest

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

pytestmark = pytest.mark.access_integration


def test_compare_uses_frozen_before_and_after_and_exact_scope(artifact_http_environment, monkeypatch):
    root, owner, members, _, _ = artifact_http_environment
    output = root / 'outputs' / 'topic'
    before, after = b'opening\nold lines\n', b'opening\nnew lines\n'
    text = output / 'script.txt'
    text.write_bytes(before)
    (output / 'removed.txt').write_text('old entry\n')
    monkeypatch.setattr(web, '_chat_backend', lambda _: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'workspace-write'})

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('native-turn-comparison')
            text.write_bytes(after)
            (output / 'removed.txt').unlink()
            (output / 'added.txt').write_text('new entry\n')
            (output / 'binary.fixture').write_bytes(b'\x00binary')
            emit('token', 'Done')

    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    body = {'sessionId': 'compare-session', 'turnId': 'compare-turn', 'message': 'Revise script'}
    response = owner.post('/api/chat/stream', json=body)
    assert response.status_code == 200, response.text
    [event] = [json.loads(line[6:]) for line in response.text.splitlines()
               if line.startswith('data: {') and '"kind": "changes"' in line]
    by_path = {row['path']: row for row in event['changes']['files']}
    assert by_path['topic/script.txt']['compareAvailable'] is True
    assert not by_path['topic/binary.fixture'].get('compareAvailable')
    text.write_bytes(b'mutable replacement')
    path = '/api/chat/turn-changes/compare-turn/compare'
    params = {'path': 'topic/script.txt'}
    comparison = owner.get(path, params=params)
    assert comparison.status_code == 200, comparison.text
    value = comparison.json()
    assert value['turnId'] == body['turnId'] and value['sessionId'] == body['sessionId']
    assert value['before'] == {'sha256': hashlib.sha256(before).hexdigest(), 'size': len(before)}
    assert value['after'] == {'sha256': hashlib.sha256(after).hexdigest(), 'size': len(after)}
    assert value['diffStatus'] == 'text' and '-old lines' in value['diff'] and '+new lines' in value['diff']
    assert 'mutable' not in value['diff']
    binary = owner.get(path, params={'path': 'topic/binary.fixture'}).json()
    assert binary['diffStatus'] == 'binary' and 'diff' not in binary
    added = owner.get(path, params={'path': 'topic/added.txt'}).json()
    deleted = owner.get(path, params={'path': 'topic/removed.txt'}).json()
    assert added['before'] is None and added['after']['size'] == 10 and '+new entry' in added['diff']
    assert deleted['after'] is None and deleted['before']['size'] == 10 and '-old entry' in deleted['diff']
    assert members['operator'].get(path, params=params).status_code == 404
    assert members['viewer'].get(path, params=params).status_code == 403
    other = owner.post('/api/projects', json={'name': 'Other comparison IP'}).json()['id']
    assert owner.get(f'/api/projects/{other}' + path.removeprefix('/api'), params=params).status_code == 404
    assert owner.get(path, params={'path': '../topic/script.txt'}).status_code == 422
    monkeypatch.setattr(web, '_secret_masks', lambda: [('old lines', '[masked]')])
    scrubbed = owner.get(path, params=params).json()
    assert scrubbed['redacted'] is True and 'old lines' not in scrubbed['diff']
    assert scrubbed['before'] == value['before'] and scrubbed['after'] == value['after']
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        sidecar = web._job_event_file(body['turnId']).with_suffix('.changes.json')
        assert sidecar.is_file()
        sidecar.write_text(sidecar.read_text().replace('old lines', 'forged evidence'))
    assert owner.get(path, params=params).status_code == 409
    assert owner.delete('/api/chat/sessions/' + body['sessionId']).status_code == 200
    assert owner.get(path, params=params).status_code == 404


def test_compare_storage_failure_does_not_offer_broken_action_or_hide_changes(artifact_http_environment, monkeypatch):
    root, owner, _, _, _ = artifact_http_environment
    output = root / 'outputs' / 'topic' / 'observed.txt'
    monkeypatch.setattr(web, '_chat_backend', lambda _: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'workspace-write'})

    def fail(*_):
        raise OSError('no comparison storage')

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('native-no-comparison')
            output.write_text('finished\n')
            emit('token', 'Done')

    monkeypatch.setattr(web, 'freeze_turn_output_comparisons', fail)
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    response = owner.post('/api/chat/stream', json={'sessionId': 'no-compare-session',
                          'turnId': 'no-compare-turn', 'message': 'Produce text'})
    assert response.status_code == 200, response.text
    [event] = [json.loads(line[6:]) for line in response.text.splitlines()
               if line.startswith('data: {') and '"kind": "changes"' in line]
    [file] = event['changes']['files']
    assert file['path'] == 'topic/observed.txt' and not file.get('compareAvailable')
    assert owner.get('/api/chat/turn-changes/no-compare-turn/compare',
                     params={'path': file['path']}).status_code == 404
