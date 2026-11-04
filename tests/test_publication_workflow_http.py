"""B30 HTTP approval binds the actual actor/account, exact draft and pinned media."""
import time
import pytest
from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

pytestmark = pytest.mark.access_integration


@pytest.fixture
def publication_environment(artifact_http_environment, monkeypatch):
    root, owner, members, client, _ = artifact_http_environment
    monkeypatch.setattr(web, '_publication_dir', lambda: root.with_name(root.name + '-host-state'))
    monkeypatch.setattr(web, '_require_automated_publishing', lambda: None)
    def unexpected_process(*args, **kwargs):
        pytest.fail('Workflow contract tests must never launch a real platform process')
    monkeypatch.setattr(web.subprocess, 'run', unexpected_process)
    async def unexpected_managed_process(*args, **kwargs):
        unexpected_process()
    monkeypatch.setattr(web, 'run_publication_process', unexpected_managed_process, raising=False)
    async def identity(platform):
        return {'id': 'actual-account-1', 'name': 'Verified Account'}
    monkeypatch.setattr(web, '_publication_account', identity, raising=False)
    sent = []
    async def dispatch(platform, req, *, media_paths=None, operation_id=None, expected_account=None,
                       approved_article=None, approved_media=None, payload_hash=None):
        from pathlib import Path
        sent.append({'platform': platform, 'title': req.title, 'body': req.body,
            'media': [Path(path).read_bytes() for path in media_paths or []], 'operationId': operation_id,
            'draft': req.draft})
        return {'status': 'submitted', 'message': 'Submitted; public visibility not verified'}
    monkeypatch.setattr(web, '_dispatch_publish', dispatch, raising=False)
    return root, owner, members, client, sent


def prepare(owner, **changes):
    payload = {'title': 'Exact title', 'body': 'Exact body', 'media': [], **changes}
    response = owner.post('/api/publish/zhihu/prepare', json=payload)
    assert response.status_code == 200, response.text
    return payload, response.json()


def approve(owner, record):
    response = owner.post('/api/publication-approvals/' + record['id'] + '/decision', json={
        'approved': True, 'payloadHash': record['payloadHash']})
    assert response.status_code == 200, response.text
    return response.json()


def wait_result(owner, record):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = owner.get('/api/publication-approvals/' + record['id'])
        assert response.status_code == 200, response.text
        current = response.json()
        if current.get('result') and current['result'].get('status') != 'executing':
            return current
        time.sleep(.01)
    pytest.fail('Approved operation never reached a recorded result')


def test_publish_requires_server_approval_before_dispatch(publication_environment):
    _, owner, _, _, sent = publication_environment
    response = owner.post('/api/publish/zhihu', json={'title': 'Direct attempt', 'body': 'Draft'})
    assert response.status_code == 428
    assert sent == []


@pytest.mark.parametrize('draft', [False, True])
def test_toutiao_mode_is_frozen_and_preserved_through_dispatch(publication_environment, draft):
    _, owner, _, _, sent = publication_environment
    with owner:
        payload = {'title': 'Exact title', 'body': 'Exact body', 'draft': draft}
        response = owner.post('/api/publish/toutiao/prepare', json=payload)
        assert response.status_code == 200, response.text
        record = response.json()
        assert record['subject']['mode'] == ('create_draft' if draft else 'publish_now')
        approve(owner, record)
        response = owner.post('/api/publish/toutiao', json={**payload,
            'approvalId': record['id'], 'payloadHash': record['payloadHash']})
        assert response.status_code == 200, response.text
        wait_result(owner, record)
        assert len(sent) == 1 and sent[0]['draft'] is draft


def test_changing_toutiao_draft_to_public_invalidates_approval(publication_environment):
    _, owner, _, _, sent = publication_environment
    with owner:
        payload = {'title': 'Exact title', 'body': 'Exact body', 'draft': True}
        response = owner.post('/api/publish/toutiao/prepare', json=payload)
        assert response.status_code == 200, response.text
        record = response.json()
        approve(owner, record)
        response = owner.post('/api/publish/toutiao', json={**payload, 'draft': False,
            'approvalId': record['id'], 'payloadHash': record['payloadHash']})
        assert response.status_code == 409, response.text
        assert sent == []


def test_unsupported_draft_mode_is_rejected_before_preparation(publication_environment):
    _, owner, _, _, sent = publication_environment
    with owner:
        response = owner.post('/api/publish/zhihu/prepare', json={
            'title': 'Exact title', 'body': 'Exact body', 'draft': True})
        assert response.status_code == 400, response.text
        assert sent == []


def test_exact_reviewed_draft_executes_once_and_cannot_be_shared_with_another_actor(publication_environment):
    _, owner, members, client, sent = publication_environment
    with owner:
        payload, record = prepare(owner)
        assert record['state'] == 'pending' and not sent
        assert record['subject']['account'] == {'id': 'actual-account-1', 'name': 'Verified Account'}
        assert record['subject']['project']['id'] == 'default'
        assert record['subject']['body'] == payload['body']
        assert members['operator'].get('/api/publication-approvals/' + record['id']).status_code == 404
        assert members['viewer'].get('/api/publication-approvals/' + record['id']).status_code == 403
        assert client().get('/api/publication-approvals/' + record['id']).status_code == 401
        approval = approve(owner, record)
        assert approval['state'] == 'approved' and sent == []
        request = {**payload, 'approvalId': record['id'], 'payloadHash': record['payloadHash']}
        response = owner.post('/api/publish/zhihu', json=request)
        assert response.status_code == 200, response.text
        final = wait_result(owner, record)
        assert final['result']['status'] == 'submitted'
        assert len(sent) == 1 and sent[0]['body'] == payload['body']
        replay = owner.post('/api/publish/zhihu', json=request)
        assert replay.status_code == 200, replay.text
        assert len(sent) == 1
        assert replay.json()['operationId'] == final['operationId']


@pytest.mark.parametrize('field,value', [('title', 'Different title'), ('body', 'Different body'), ('tags', 'different-tag')])
def test_changed_draft_invalidates_old_approval(publication_environment, field, value):
    _, owner, _, _, sent = publication_environment
    payload, record = prepare(owner)
    approve(owner, record)
    response = owner.post('/api/publish/zhihu', json={**payload, field: value,
        'approvalId': record['id'], 'payloadHash': record['payloadHash']})
    assert response.status_code == 409, response.text
    assert owner.get('/api/publication-approvals/' + record['id']).json()['state'] == 'invalidated'
    assert not sent


def test_changed_actual_account_requires_new_approval(publication_environment, monkeypatch):
    _, owner, _, _, sent = publication_environment
    payload, record = prepare(owner)
    approve(owner, record)
    async def changed(platform):
        return {'id': 'actual-account-2', 'name': 'Another Account'}
    monkeypatch.setattr(web, '_publication_account', changed)
    response = owner.post('/api/publish/zhihu', json={**payload,
        'approvalId': record['id'], 'payloadHash': record['payloadHash']})
    assert response.status_code == 409
    assert not sent


def test_approved_media_survives_source_overwrite_and_delete(publication_environment):
    root, owner, _, _, sent = publication_environment
    path = root / 'outputs/topic/cover.png'
    path.write_bytes(b'APPROVED_IMAGE_BYTES')
    with owner:
        payload, record = prepare(owner, media=['topic/cover.png'])
        media = record['subject']['media'][0]
        assert media['revision'] >= 1 and len(media['sha256']) == 64
        path.write_bytes(b'NEW_UNAPPROVED_IMAGE')
        path.unlink()
        approve(owner, record)
        response = owner.post('/api/publish/zhihu', json={**payload,
            'approvalId': record['id'], 'payloadHash': record['payloadHash']})
        assert response.status_code == 200, response.text
        assert wait_result(owner, record)['result']['status'] == 'submitted'
        assert sent[0]['media'] == [b'APPROVED_IMAGE_BYTES']


def test_changed_media_selection_cannot_reuse_reviewed_version(publication_environment):
    root, owner, _, _, sent = publication_environment
    (root / 'outputs/topic/a.png').write_bytes(b'A')
    (root / 'outputs/topic/b.png').write_bytes(b'B')
    payload, record = prepare(owner, media=['topic/a.png'])
    approve(owner, record)
    response = owner.post('/api/publish/zhihu', json={**payload, 'media': ['topic/b.png'],
        'approvalId': record['id'], 'payloadHash': record['payloadHash']})
    assert response.status_code == 409, response.text
    assert not sent


def test_client_cannot_forge_approval_actor_or_result(publication_environment):
    _, owner, _, _, sent = publication_environment
    _, record = prepare(owner)
    response = owner.post('/api/publication-approvals/' + record['id'] + '/decision', json={
        'approved': True, 'payloadHash': record['payloadHash'], 'actorId': 'forged', 'state': 'approved'})
    assert response.status_code == 422
    assert not sent


def test_active_operation_prevents_replacing_its_platform_profile(publication_environment, monkeypatch):
    root, owner, _, _, sent = publication_environment
    monkeypatch.setattr(web, '_browser_profiles', lambda: root / 'test-only-browser-profiles')
    monkeypatch.setattr(web, '_cookie_path', lambda: root / 'test-only-cookie.json')
    monkeypatch.setattr(web, 'LOGIN_PROCESSES', {})
    profile = root / 'test-only-browser-profiles' / web.LOGIN_RUNNERS['zhihu']['profile']
    profile.mkdir(parents=True)
    (profile / 'sentinel').write_text('must remain')
    payload, record = prepare(owner)
    approve(owner, record)
    from easel.request_identity import actor_context
    from easel.projects import project_context
    actor = owner.get('/api/auth/me').json()
    with actor_context(actor), project_context('default', root):
        claimed = web._publication_workflow().call('claim', record['id'], current_subject=record['subject'])
        assert claimed['claimed']
    response = owner.post('/api/logout/zhihu')
    assert response.status_code == 409, response.text
    assert (profile / 'sentinel').read_text() == 'must remain'
    assert not sent


def test_literal_hash_filename_is_compared_as_a_path_not_a_url(publication_environment):
    root, owner, _, _, sent = publication_environment
    relative = 'topic/cover#draft.png'
    (root / 'outputs' / relative).write_bytes(b'APPROVED_HASH_FILENAME')
    with owner:
        payload, record = prepare(owner, media=[relative])
        approve(owner, record)
        response = owner.post('/api/publish/zhihu', json={**payload,
            'approvalId': record['id'], 'payloadHash': record['payloadHash']})
        assert response.status_code == 200, response.text
        assert wait_result(owner, record)['result']['status'] == 'submitted'
        assert sent[0]['media'] == [b'APPROVED_HASH_FILENAME']


def test_operation_sms_is_private_and_cannot_target_another_approval(publication_environment):
    import json
    import stat
    from easel.request_identity import actor_context
    from easel.projects import project_context
    root, owner, members, _, sent = publication_environment
    (root / 'outputs/topic/clip.mp4').write_bytes(b'SMS_TEST_VIDEO')
    response = owner.post('/api/publish/douyin/prepare', json={
        'title': 'SMS test', 'body': 'SMS test', 'media': ['topic/clip.mp4']})
    assert response.status_code == 200, response.text
    record = response.json(); approve(owner, record)
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        workflow = web._publication_workflow()
        record = workflow.call('claim', record['id'], current_subject=record['subject'])
        directory = workflow.operation_dir(record)
        directory.mkdir(parents=True)
        workflow.status_path(record).write_text(json.dumps({'state': 'sms_required', 'message': 'test'}))
        code_file = directory / 'runner.code'
        code_file.write_text('previous')
        code_file.chmod(0o644)
    response = owner.post('/api/publish/douyin/sms?approvalId=' + record['id'], json={'code': '123456'})
    assert response.status_code == 200, response.text
    assert code_file.read_text() == '123456'
    assert stat.S_IMODE(code_file.stat().st_mode) == 0o600
    assert members['operator'].post('/api/publish/douyin/sms?approvalId=' + record['id'], json={'code': '654321'}).status_code == 404
    assert code_file.read_text() == '123456'
    assert not sent
