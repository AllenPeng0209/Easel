"""C17 technical archives include only actual scoped and safely projected logs."""
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_chat_artifacts import artifact_http_environment
from web.chat_history import ChatHistoryStore

ROUTE = '/api/chat/sessions/source/export/logs'


def write_journal(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(record) + '\n' for record in records))


def seed(root):
    catalog = ChatHistoryStore(root)
    catalog.merge([{'id': 'source', 'title': 'private title',
                    'messages': [{'role': 'assistant', 'content': 'private transcript', 'clientTurnId': 'legacy'}]}])
    journal = root / 'jobs' / 'claimed.jsonl'
    catalog.claim_turn('claimed', 'source', 'private digest', journal)
    write_journal(journal, [
        {'id': 1, 'event': 'admitted', 'data': {'turnId': 'claimed', 'sessionId': 'source'}},
        {'id': 2, 'event': 'token', 'data': 'private provider token'},
        {'id': 3, 'event': 'agent_event', 'data': {'kind': 'tool', 'name': 'bash', 'callId': 'private unknown secret',
                                                  'status': 'failed', 'exitCode': 2, 'input': {'opaque': 'private input'},
                                                  'output': 'private stdout', 'providerError': 'private error'}},
        {'id': 4, 'event': 'unknown-private-kind', 'data': {'unknown': 'private payload'}},
        {'id': 5, 'event': 'admitted', 'data': {'turnId': 'sibling', 'sessionId': 'foreign'}},
        {'id': 6, 'event': 'done', 'data': {'sessionKey': 'source', 'outcome': 'completed'}},
    ])
    with journal.open('ab') as out:
        out.write(b'{"id": 7, "event": "token", "data": "private torn')
    write_journal(root / 'jobs' / 'legacy.jsonl', [
        {'id': 1, 'event': 'activity', 'data': 'private activity'},
        {'id': 2, 'event': 'done', 'data': {'sessionKey': 'source'}},
    ])
    sibling = root / 'jobs' / 'sibling.jsonl'
    catalog.claim_turn('sibling', 'other', 'sibling digest', sibling)
    write_journal(sibling, [{'id': 1, 'event': 'done', 'data': {'sessionKey': 'source', 'outcome': 'failed'}}])
    catalog.claim_turn('missing', 'source', 'missing digest', root / 'jobs' / 'missing.jsonl')
    (root / 'jobs' / 'missing.jsonl').unlink()
    return catalog


def client_for(root, *, admin=True, authorize=None):
    from web.chat_session_export import create_router

    app = FastAPI()
    @app.middleware('http')
    async def actor(request, call_next):
        request.state.chat_export_authorized = authorize
        with actor_context({'id': 'owner', 'is_admin': admin}):
            return await call_next(request)
    app.include_router(create_router(lambda: root, require_admin=lambda: None))
    return TestClient(app)


def read_zip(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('application/zip')
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert all(not name.startswith('/') and '..' not in name.split('/') for name in archive.namelist())
        entries = {name: archive.read(name) for name in archive.namelist()}
    return entries, json.loads(entries['manifest.json'])


def test_technical_zip_projects_real_records_with_no_freeform_or_foreign_payload(tmp_path):
    seed(tmp_path)
    entries, manifest = read_zip(client_for(tmp_path).get(ROUTE))
    assert set(entries) == {'manifest.json', 'journal.jsonl'}
    assert manifest['journal']['exportedTurns'] == 2
    assert manifest['journal']['missingClaimedJournals'] == 1
    assert manifest['journal']['incompleteLines'] == 1
    assert manifest['journal']['conflictingAdmissionRecords'] == 1
    assert manifest['includesReadableTranscript'] is False
    assert b'private' not in b''.join(entries.values())
    records = [json.loads(line) for line in entries['journal.jsonl'].splitlines()]
    assert [event['event'] for event in records] == ['admitted', 'token', 'agent_event', 'unknown', 'done', 'activity', 'done']
    tool = records[2]['data']
    assert tool['kind'] == 'tool' and tool['name'] == 'bash' and tool['status'] == 'failed' and tool['exitCode'] == 2
    assert tool['callId'] and len({event['turnRef'] for event in records}) == 2


def test_symlink_and_empty_evidence_yield_manifest_only(tmp_path):
    catalog = ChatHistoryStore(tmp_path / 'catalog')
    catalog.merge([{'id': 'source', 'messages': []}])
    outside = tmp_path / 'outside'
    outside.mkdir()
    write_journal(outside / 'foreign.jsonl', [{'id': 1, 'event': 'done', 'data': {'sessionKey': 'source'}}])
    (catalog.path.parent / 'jobs').symlink_to(outside, target_is_directory=True)
    entries, manifest = read_zip(client_for(catalog.path.parent).get(ROUTE))
    assert set(entries) == {'manifest.json'} and manifest['journal']['exportedTurns'] == 0


def test_claimed_symlink_file_is_not_read(tmp_path):
    catalog = ChatHistoryStore(tmp_path / 'catalog')
    catalog.merge([{'id': 'source', 'messages': []}])
    source = tmp_path / 'foreign.jsonl'
    write_journal(source, [{'id': 1, 'event': 'done', 'data': {'sessionKey': 'source', 'outcome': 'failed'}}])
    link = catalog.path.parent / 'jobs' / 'claimed.jsonl'
    catalog.claim_turn('claimed', 'source', 'digest', link)
    link.unlink()
    link.symlink_to(source)
    entries, manifest = read_zip(client_for(catalog.path.parent).get(ROUTE))
    assert set(entries) == {'manifest.json'} and manifest['journal']['missingClaimedJournals'] == 1


def test_only_platform_admin_and_live_catalog_can_download(tmp_path):
    catalog = seed(tmp_path)
    assert client_for(tmp_path, admin=False).get(ROUTE).status_code == 403
    assert client_for(tmp_path, authorize=lambda: False).get(ROUTE).status_code == 403
    live_access = iter((True, False))
    assert client_for(tmp_path, authorize=lambda: next(live_access)).get(ROUTE).status_code == 403
    catalog.delete('source')
    assert client_for(tmp_path).get(ROUTE).status_code == 404


def test_head_has_same_scope_but_never_builds_zip(tmp_path, monkeypatch):
    from web import chat_session_export as exporter

    seed(tmp_path)
    monkeypatch.setattr(exporter, '_build_archive', lambda *_: pytest.fail('HEAD must not create ZIP'))
    response = client_for(tmp_path).head(ROUTE)
    assert response.status_code == 200 and not response.content
    assert response.headers['content-type'] == 'application/zip'
    assert 'attachment; filename=' in response.headers['content-disposition']
    assert 'content-length' not in response.headers
    assert client_for(tmp_path, admin=False).head(ROUTE).status_code == 403
    assert client_for(tmp_path).head(ROUTE.replace('/source/', '/unknown/')).status_code == 404


@pytest.mark.access_integration
def test_production_export_is_current_actor_and_ip_scoped(artifact_http_environment):
    import web.app as web
    root, owner, members, anonymous, _ = artifact_http_environment
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        seed(web._sessions_dir())
    assert anonymous().get(ROUTE).status_code == 401
    assert members['operator'].get(ROUTE).status_code == 403
    other = owner.post('/api/projects', json={'name': 'Other export IP'}).json()['id']
    assert owner.get(f'/api/projects/{other}' + ROUTE.removeprefix('/api')).status_code == 404
    assert read_zip(owner.get(ROUTE))[1]['journal']['exportedTurns'] == 2
