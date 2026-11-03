"""A01 UTF-16 file-reference sidecars retain session receipt authorization."""
import hashlib
import json

import pytest

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_chat_artifacts import artifact_http_environment
from tests.test_chat_topic_reference import install_runner
from web.chat_history import ChatHistoryStore
import web.app as web

pytestmark = pytest.mark.access_integration


def units(text):
    return len(text.encode('utf-16-le')) // 2


def upload(client, name='资料🎞.txt', session='file-session'):
    response = client.post('/api/upload', data={'sessionId': session},
                           files={'files': (name, b'owned file content', 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()['files'][0]


def submission(attachment, *, session='file-session', turn='file-turn'):
    prefix = '  🌱请看 '
    label = '@' + attachment['name']
    return {'message': prefix + label + '  ', 'sessionId': session, 'turnId': turn,
            'attachments': [attachment], 'references': [
                {'start': units(prefix), 'end': units(prefix + label), 'attachmentId': attachment['id']},
            ]}


@pytest.mark.parametrize('runtime', ['api', 'codex'])
def test_verified_utf16_reference_preserves_original_text_and_reaches_runner(
        artifact_http_environment, monkeypatch, runtime):
    _, owner, _, _, _ = artifact_http_environment
    attachment = upload(owner)
    request = submission(attachment)
    prompts = install_runner(monkeypatch, runtime)
    response = owner.post('/api/chat/stream', json=request)
    assert response.status_code == 200, response.text
    prompt = prompts[0]
    if runtime == 'api':
        assert prompt[0]['text'] == request['message']
        text = next(block['text'] for block in prompt if '<attachment_references>' in block.get('text', ''))
        assert 'owned file content' in str(prompt)
    else:
        assert prompt.startswith(request['message'])
        text = prompt
    raw = text.split('<attachment_references>\n', 1)[1].split('\n</attachment_references>', 1)[0]
    observed = json.loads(raw)[0]
    assert observed == {**request['references'][0], 'label': '@' + attachment['name'], 'attachmentNumber': 1,
                        **({'path': 'outputs/' + attachment['path']} if runtime == 'codex' else {})}


@pytest.mark.parametrize('change', ['out-of-bounds', 'surrogate', 'overlap', 'not-label', 'unknown-receipt', 'forged-name'])
def test_bad_reference_rejects_before_claim_and_can_resubmit_corrected_turn(artifact_http_environment, monkeypatch, change):
    _, owner, _, _, _ = artifact_http_environment
    attachment = upload(owner)
    valid = submission(attachment)
    prompt = install_runner(monkeypatch, 'api')
    bad = {**valid, 'references': [dict(valid['references'][0])]}
    reference = bad['references'][0]
    if change == 'out-of-bounds':
        reference['end'] += 100
    elif change == 'surrogate':
        reference['start'] = 3  # Between the two UTF-16 units of the first emoji.
    elif change == 'overlap':
        bad['references'].append(dict(reference))
    elif change == 'not-label':
        bad['message'] = valid['message'].replace('@', '!')
    elif change == 'unknown-receipt':
        reference['attachmentId'] = 'f' * 24
    else:
        fake = {**attachment, 'name': 'fake.txt'}
        bad = submission(fake)
    rejected = owner.post('/api/chat/stream', json=bad)
    assert rejected.status_code == 400, rejected.text
    assert not prompt
    assert owner.post('/api/chat/stream', json=valid).status_code == 200
    assert len(prompt) == 1


def test_reference_does_not_escape_actor_ip_or_session(artifact_http_environment, monkeypatch):
    _, owner, members, _, _ = artifact_http_environment
    attachment = upload(owner)
    prompts = install_runner(monkeypatch, 'api')
    assert owner.post('/api/chat/stream', json=submission(attachment, session='another-session')).status_code == 403
    assert members['operator'].post('/api/chat/stream', json=submission(attachment)).status_code == 403
    ip = owner.post('/api/projects', json={'name': 'Unrelated reference IP'}).json()['id']
    assert owner.post(f'/api/projects/{ip}/chat/stream', json=submission(attachment)).status_code == 404
    assert not prompts


def test_attachment_search_returns_current_scoped_receipts_only(artifact_http_environment, monkeypatch):
    root, owner, members, _, _ = artifact_http_environment
    receipt = upload(owner, 'Meeting 资料.txt')
    upload(owner, 'Other 资料.txt', session='another-session')
    upload(owner, 'Unrelated.txt')
    path = root / 'outputs' / receipt['path']
    (path.parent / 'Meeting shortcut.txt').symlink_to(path)
    response = owner.get('/api/chat/attachment-search', params={'sessionId': 'file-session', 'query': 'meeting 资料'})
    assert response.status_code == 200, response.text
    assert response.json() == {'files': [{**receipt, 'mime': 'text/plain', 'size': path.stat().st_size}], 'candidates': []}
    query = {'sessionId': 'file-session', 'query': 'meeting'}
    ip = owner.post('/api/projects', json={'name': 'Other attachment scope'}).json()['id']
    assert owner.get(f'/api/projects/{ip}/chat/attachment-search', params=query).json() == {'files': [], 'candidates': []}
    assert members['operator'].get('/api/chat/attachment-search', params=query).json() == {'files': [], 'candidates': []}
    assert members['viewer'].get('/api/chat/attachment-search', params=query).status_code == 403
    prompts = install_runner(monkeypatch, 'api')
    assert owner.post('/api/chat/stream', json=submission(response.json()['files'][0])).status_code == 200
    assert prompts


def test_attachment_library_pick_copies_verified_source_and_is_bound_to_scope(artifact_http_environment):
    root, owner, members, _, _ = artifact_http_environment
    library = root / 'outputs' / 'Production' / 'assets'
    library.mkdir(parents=True, exist_ok=True)
    source = library / 'Meeting notes.txt'
    source.write_text('fixed source bytes')
    (library / 'credentials.txt').write_text('private data')
    (library / 'Meeting shortcut.txt').symlink_to(source)
    params = {'sessionId': 'file-session', 'query': 'Meeting'}
    response = owner.get('/api/chat/attachment-search', params=params)
    assert response.status_code == 200, response.text
    [candidate] = response.json()['candidates']
    assert candidate == {'id': candidate['id'], 'name': source.name, 'mime': 'text/plain',
                         'size': source.stat().st_size, 'context': 'Production/assets'}
    assert str(source) not in response.text and 'credentials' not in response.text
    assert owner.get('/api/chat/attachment-search', params={**params, 'query': 'credentials'}).json()['candidates'] == []
    pick = {'sessionId': 'file-session', 'candidateId': candidate['id']}
    assert owner.post('/api/chat/attachment-select', json={**pick, 'path': str(source)}).status_code == 422
    assert owner.post('/api/chat/attachment-select', json={**pick, 'sessionId': 'another'}).status_code == 404
    assert members['operator'].post('/api/chat/attachment-select', json=pick).status_code == 404
    assert members['viewer'].post('/api/chat/attachment-select', json=pick).status_code == 403
    ip = owner.post('/api/projects', json={'name': 'Other library IP'}).json()['id']
    assert owner.post(f'/api/projects/{ip}/chat/attachment-select', json=pick).status_code == 404
    selected = owner.post('/api/chat/attachment-select', json=pick)
    assert selected.status_code == 200, selected.text
    receipt = selected.json()['file']
    copied = root / 'outputs' / receipt['path']
    assert receipt['name'] == source.name and receipt['path'].startswith('_inbox/')
    assert receipt['mime'] == 'text/plain' and receipt['size'] == source.stat().st_size
    assert owner.post('/api/chat/attachment-select', json=pick).json() == selected.json()
    assert list(copied.parent.iterdir()) == [copied]
    assert owner.get('/api/chat/attachment-search', params=params).json()['files'] == [receipt]
    source.write_text('changed source')
    assert copied.read_text() == 'fixed source bytes'
    assert owner.post('/api/chat/attachment-select', json=pick).status_code == 404


def test_attachment_search_obeys_normal_upload_limit(artifact_http_environment, monkeypatch):
    root, owner, _, _, _ = artifact_http_environment
    library = root / 'outputs' / 'Production'
    library.mkdir()
    (library / 'large.txt').write_text('material')
    params = {'sessionId': 'size-test', 'query': 'large'}
    [candidate] = owner.get('/api/chat/attachment-search', params=params).json()['candidates']
    monkeypatch.setattr(web, 'MAX_UPLOAD_MB', 0)
    assert owner.get('/api/chat/attachment-search', params=params).json()['candidates'] == []
    assert owner.post('/api/chat/attachment-select', json={
        'sessionId': 'size-test', 'candidateId': candidate['id'],
    }).status_code == 404


def test_typed_catalog_references_share_exact_label_and_receipt_validation(artifact_http_environment):
    root, owner, members, _, _ = artifact_http_environment
    attachment = upload(owner)
    req = submission(attachment)
    row = {'role': 'user', 'content': req['message'], 'attachments': [attachment], 'references': req['references']}
    catalog = {'sessions': [{'id': req['sessionId'], 'updated': 10, 'messages': [row]}]}
    (root / 'outputs' / attachment['path']).unlink()
    result = owner.put('/api/chat/sessions', json=catalog)
    assert result.status_code == 200, result.text
    assert result.json()['sessions'][0]['messages'][0]['references'] == req['references']
    bad = {'sessions': [{**catalog['sessions'][0], 'updated': 11,
                         'messages': [{**row, 'attachments': []}]}]}
    assert owner.put('/api/chat/sessions', json=bad).status_code == 400
    assert members['operator'].put('/api/chat/sessions', json=catalog).status_code == 403
    queued = {'sessions': [{'id': req['sessionId'], 'updated': 11, 'messages': [row],
                            'queued': [{'id': 'queue', 'text': req['message'], 'attachments': [attachment],
                                        'references': req['references']}]}]}
    result = owner.put('/api/chat/sessions', json=queued)
    assert result.status_code == 200, result.text
    assert result.json()['sessions'][0]['queued'][0]['references'] == req['references']


def test_absent_sidecar_keeps_pre_a01_turn_claim_digest(artifact_http_environment, monkeypatch):
    root, owner, _, _, _ = artifact_http_environment
    prompts = install_runner(monkeypatch, 'api')
    request = {'message': 'prior user message', 'sessionId': 'legacy-session', 'turnId': 'legacy-turn'}
    prior = {**request, 'persona': None, 'attachments': [], 'autonomyMode': 'autonomous'}
    digest = hashlib.sha256(json.dumps({'request': prior, 'runtime': 'api'},
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        catalog = ChatHistoryStore(web._sessions_dir())
        path = web._job_event_file(request['turnId'])
        assert catalog.claim_turn(request['turnId'], request['sessionId'], digest, path)[0]
        path.write_text(json.dumps({'id': 1, 'event': 'done', 'data': {'sessionKey': 'legacy-session'}}) + '\n')
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert owner.post('/api/chat/stream', json={**request, 'references': []}).status_code == 200
    assert not prompts


def test_sidecar_participates_in_turn_identity_and_exact_replay_never_reopens_file(
        artifact_http_environment, monkeypatch):
    root, owner, _, _, _ = artifact_http_environment
    attachment = upload(owner)
    request = submission(attachment)
    label = '@' + attachment['name']
    prefix = request['message'] + '再看 '
    request['message'] = prefix + label
    prompts = install_runner(monkeypatch, 'api')
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    (root / 'outputs' / attachment['path']).unlink()
    alternate = {**request, 'references': [{'start': units(prefix), 'end': units(prefix + label),
                                           'attachmentId': attachment['id']}]}
    assert owner.post('/api/chat/stream', json=alternate).status_code == 409
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert len(prompts) == 1


@pytest.mark.parametrize('extra', [{'start': True}, {'end': '9'}, {'path': '../ignored.txt'}])
def test_file_sidecar_schema_rejects_coercion_and_extra_authority(artifact_http_environment, extra):
    _, owner, _, _, _ = artifact_http_environment
    request = submission(upload(owner))
    request['references'][0].update(extra)
    assert owner.post('/api/chat/stream', json=request).status_code == 422
