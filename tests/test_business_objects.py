"""P0/D25 read-only DTOs over real isolated local domain stores."""
import hashlib
import importlib
import json
import sqlite3

import pytest
from fastapi import HTTPException
from easel.chat_artifacts import ArtifactError, ChatArtifactStore
from easel.projects import project_context, project_id
from easel.request_identity import actor_context, current_user
from easel.video_workshop import WorkshopStore


def service(host):
    return importlib.import_module('web.business_objects').BusinessObjectService(host)


class Host:
    def __init__(self, root):
        self.PROJECT_ROOT = root
        self.roles = {('actor-one', 'ip-one'): 'owner', ('actor-two', 'ip-one'): 'operator',
                      ('viewer', 'ip-one'): 'viewer', ('actor-one', 'ip-two'): 'owner'}
        self.checks = 0
        self.on_read = None

    def _require_operation(self):
        self.checks += 1
        actor = current_user()
        if not actor:
            raise HTTPException(401, 'login required')
        role = self.roles.get((actor['id'], project_id()))
        if role is None:
            raise HTTPException(404, 'IP not available')
        if role not in {'owner', 'operator'}:
            raise HTTPException(403, 'operation denied')

    def _workspace(self):
        return self.PROJECT_ROOT / project_id()

    def _outputs_dir(self):
        return self._workspace() / 'outputs'

    def _chat_artifact_store(self):
        return ChatArtifactStore(self._outputs_dir() / '_sessions' / current_user()['id'],
                                 self._outputs_dir(), self._workspace(), project_id())

    def _read_ideas(self):
        path = self._outputs_dir() / '_ideas.json'
        values = json.loads(path.read_text()) if path.exists() else []
        if self.on_read:
            self.on_read()
        return values


@pytest.fixture
def host(tmp_path):
    value = Host(tmp_path)
    with actor_context({'id': 'actor-one'}), project_context('ip-one', tmp_path):
        value._outputs_dir().mkdir(parents=True)
        yield value


def ideas(host, values):
    (host._outputs_dir() / '_ideas.json').write_text(json.dumps(values))


def draft(host, name='article.md', data=b'first version', *, call='call'):
    path = host._outputs_dir() / 'content' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return host._chat_artifact_store().present([{'path': 'outputs/content/' + name, 'description': '内容草稿'}],
        session_id='session', turn_id='turn', call_id=call)[0]


def workshop(host, **changes):
    store = WorkshopStore(host._outputs_dir(), strict_canvas_bindings=True)
    project = store.create({'title': '真实本地工坊', 'brief': '视频brief'}, canvas_creator=lambda _: 'canvas-one')
    project.update(changes)
    store._write(project)
    return store, project


def test_idea_done_is_not_content_or_publish_completion(host):
    ideas(host, [{'id': 'idea-one', 'title': '选题一', 'note': '采访方向', 'status': 'done', 'created': 123,
                  'cookie': 'mocked'}])
    response = service(host).query({'action': 'inspect', 'kind': 'idea', 'id': 'idea-one'})
    item = response['items'][0]
    assert response['schemaVersion'] == item['schemaVersion'] == 1
    assert response['actorId'] == item['actorId'] == 'actor-one'
    assert response['projectId'] == item['projectId'] == 'ip-one'
    assert item['status'] == {'code': 'idea_done', 'label': '选题已完成', 'source': 'stored'}
    assert item['version'] == {'revision': None, 'sha256': None}
    assert len(item['snapshotHash']) == 64 and type(item['observedAt']) is int
    assert item['actions'] == [{'kind': 'open', 'target': 'ideas', 'entityId': 'idea-one', 'label': '查看选题'}]
    assert 'cookie' not in str(item)


def test_snapshot_hash_is_stable_across_time_but_changes_with_source(host, monkeypatch):
    ideas(host, [{'id': 'one', 'title': 'Original', 'status': 'pending'}])
    module = importlib.import_module('web.business_objects')
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    one = service(host).query({'action': 'list', 'kind': 'idea'})['items'][0]
    monkeypatch.setattr(module.time, 'time', lambda: 2000)
    two = service(host).query({'action': 'list', 'kind': 'idea'})['items'][0]
    assert one['observedAt'] != two['observedAt'] and one['snapshotHash'] == two['snapshotHash']
    ideas(host, [{'id': 'one', 'title': 'Edited', 'status': 'pending'}])
    assert service(host).query({'action': 'list', 'kind': 'idea'})['items'][0]['snapshotHash'] != one['snapshotHash']


def test_twenty_item_pages_can_traverse_every_record_and_filter(host):
    ideas(host, [{'id': f'idea-{index:03}', 'title': f'Topic {index}', 'status': 'pending'} for index in range(47)])
    first = service(host).query({'action': 'list', 'kind': 'idea', 'cursor': '', 'query': ''})
    assert len(first['items']) == 20 and first['nextCursor']
    second = service(host).query({'action': 'list', 'kind': 'idea', 'cursor': first['nextCursor']})
    third = service(host).query({'action': 'list', 'kind': 'idea', 'cursor': second['nextCursor']})
    assert [len(second['items']), len(third['items'])] == [20, 7]
    assert third['nextCursor'] is None
    assert len({item['id'] for page in (first, second, third) for item in page['items']}) == 47
    assert [item['id'] for item in service(host).query({'action': 'list', 'kind': 'idea', 'query': 'TOPIC 46'})['items']] == ['idea-046']


@pytest.mark.parametrize('payload', [
    {'action': 'delete', 'kind': 'idea'}, {'action': 'list', 'kind': 'account'},
    {'action': 'inspect', 'kind': 'idea'}, {'action': 'inspect', 'kind': 'idea', 'id': '../one'},
    *[{'action': 'list', 'kind': 'idea', key: 'another'} for key in ('actorId', 'projectId', 'url', 'path', 'limit')],
    {'action': 'inspect', 'kind': 'idea', 'id': 'one', 'cursor': ''},
    {'action': 'inspect', 'kind': 'idea', 'id': 'one', 'query': ''},
    {'action': 'list', 'kind': 'idea', 'query': 'bad\nquery'},
    {'action': 'list', 'kind': 'idea', 'query': 'x' * 501},
    {'action': 'list', 'kind': 'idea', 'cursor': 'x' * 2049},
])
def test_strict_request_rejects_writes_paths_scope_overrides_and_extra_fields(host, payload):
    with pytest.raises(HTTPException) as caught:
        service(host).query(payload)
    assert caught.value.status_code == 400


@pytest.mark.parametrize('actor,expected', [('viewer', 403), ('stranger', 404), (None, 401)])
def test_viewer_unknown_member_and_anonymous_cannot_query(host, actor, expected):
    with actor_context({'id': actor} if actor else None):
        with pytest.raises(HTTPException) as caught:
            service(host).query({'action': 'list', 'kind': 'idea'})
        assert caught.value.status_code == expected


def test_fresh_membership_check_after_read_blocks_revoked_access(host):
    ideas(host, [{'id': 'one', 'title': 'Must not return', 'status': 'pending'}])
    host.on_read = lambda: host.roles.update({('actor-one', 'ip-one'): 'viewer'})
    with pytest.raises(HTTPException) as caught:
        service(host).query({'action': 'list', 'kind': 'idea'})
    assert caught.value.status_code == 403 and host.checks == 2


def test_cursor_binds_actor_ip_and_filter_and_current_ip_selects_source(host):
    ideas(host, [{'id': f'{number:03}', 'title': 'IP1', 'status': 'pending'} for number in range(25)])
    first = service(host).query({'action': 'list', 'kind': 'idea'})
    for actor, ip, query in [('actor-two', 'ip-one', ''), ('actor-one', 'ip-two', ''), ('actor-one', 'ip-one', 'changed')]:
        with actor_context({'id': actor}), project_context(ip, host.PROJECT_ROOT):
            with pytest.raises(HTTPException) as caught:
                service(host).query({'action': 'list', 'kind': 'idea', 'query': query, 'cursor': first['nextCursor']})
            assert caught.value.status_code == 400
    with project_context('ip-two', host.PROJECT_ROOT):
        assert service(host).query({'action': 'list', 'kind': 'idea'})['items'] == []


def test_drafts_are_private_text_snapshots_with_fixed_old_revision(host):
    first = draft(host)
    later = draft(host, data=b'new version', call='later')
    draft(host, name='not-a-draft.png', data=b'image', call='image')
    (host._outputs_dir() / 'content/article.md').unlink()
    item = service(host).query({'action': 'inspect', 'kind': 'draft', 'id': first['artifactId']})['items'][0]
    assert item['id'] == first['artifactId'] and item['status']['code'] == 'draft_file'
    assert item['version'] == {'revision': 1, 'sha256': hashlib.sha256(b'first version').hexdigest()}
    assert item['details']['artifact']['url'] == first['url']
    assert item['details']['artifact']['reviewStatus'] == 'unreviewed'
    assert {item['id'] for item in service(host).query({'action': 'list', 'kind': 'draft'})['items']} == {first['artifactId'], later['artifactId']}
    with actor_context({'id': 'actor-two'}):
        assert service(host).query({'action': 'list', 'kind': 'draft'})['items'] == []
        with pytest.raises(HTTPException) as caught:
            service(host).query({'action': 'inspect', 'kind': 'draft', 'id': first['artifactId']})
        assert caught.value.status_code == 404
    with project_context('ip-two', host.PROJECT_ROOT):
        with pytest.raises(HTTPException) as caught:
            service(host).query({'action': 'inspect', 'kind': 'draft', 'id': first['artifactId']})
        assert caught.value.status_code == 404


def test_missing_draft_blob_is_unavailable_without_current_file_fallback(host):
    metadata = draft(host)
    (host._chat_artifact_store().directory / 'artifact_blobs' / metadata['sha256']).unlink()
    item = service(host).query({'action': 'inspect', 'kind': 'draft', 'id': metadata['artifactId']})['items'][0]
    assert item['status']['code'] == 'draft_unavailable' and item['details']['artifact']['status'] == 'unavailable'


def test_corrupt_snapshot_metadata_cannot_leak_absolute_paths_or_arbitrary_urls(host):
    metadata = draft(host)
    directory = host._chat_artifact_store().directory
    with sqlite3.connect(directory / 'chat_artifacts.sqlite3') as connection:
        corrupt = {**metadata, 'path': '/private/host/file.md', 'url': 'https://example.test/unsafe'}
        connection.execute('UPDATE artifacts SET payload=? WHERE id=?', (json.dumps(corrupt), metadata['artifactId']))
    with pytest.raises(HTTPException) as caught:
        service(host).query({'action': 'inspect', 'kind': 'draft', 'id': metadata['artifactId']})
    assert caught.value.status_code == 409
    assert '/private/host' not in str(caught.value.detail)


def test_catalog_keyset_pages_keep_every_old_artifact_id(host):
    metadata = [draft(host, name=f'draft-{index}.md', call=str(index)) for index in range(24)]
    store = host._chat_artifact_store()
    first = store.list_metadata(limit=20)
    second = store.list_metadata(limit=20, after_id=first[-1]['artifactId'])
    assert len(first) == 20 and len(second) == 4
    assert {item['artifactId'] for item in first + second} == {item['artifactId'] for item in metadata}


def test_catalog_corrupt_payload_id_cannot_steer_keyset_paging(host):
    metadata = draft(host)
    store = host._chat_artifact_store()
    with sqlite3.connect(store.path) as connection:
        corrupted = {**metadata, 'artifactId': 'c' * 32}
        if metadata['artifactId'] == corrupted['artifactId']:
            corrupted['artifactId'] = 'd' * 32
        connection.execute('UPDATE artifacts SET payload=? WHERE id=?', (json.dumps(corrupted), metadata['artifactId']))
    with pytest.raises(ArtifactError) as caught:
        store.list_metadata()
    assert caught.value.status_code == 409


def test_successful_render_without_collect_is_not_a_delivery_or_review(host):
    _, project = workshop(host, renders=[{'id': 'a' * 32, 'status': 'succeeded', 'content_revision': 1,
        'engine_revision': 4, 'submission_state': 'confirmed', 'task_id': 'actual-local-task'}])
    item = service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})['items'][0]
    assert item['status']['code'] == 'render_uncollected'
    assert item['version'] == {'revision': 1, 'sha256': None}
    assert item['details']['project']['renders'][0]['status'] == 'succeeded'
    assert item['details']['project']['deliverables'] == []
    assert item['details']['engineVerification'] == 'not_checked'
    assert item['details']['publishEligibility'] == 'unverified'


@pytest.mark.parametrize('review_kind,expected', [('simulated', 'simulated_reviewed'), ('human', 'human_review_recorded')])
def test_saved_reviews_cannot_claim_current_publish_eligibility(host, review_kind, expected):
    video = host._outputs_dir() / 'video/final.mp4'
    video.parent.mkdir()
    video.write_bytes(b'local fixture video bytes')
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    _, project = workshop(host, revision=2, stage='approved' if review_kind == 'human' else 'simulated_reviewed',
        deliverables=[{'id': 'b' * 32, 'revision': 2, 'sha256': digest, 'path': 'video/final.mp4', 'engine_revision': 4}],
        reviews=[{'id': 'c' * 32, 'kind': review_kind, 'decision': 'approved', 'revision': 2,
                  'artifact_id': 'b' * 32, 'artifact_sha256': digest, 'cookie': 'mocked'}],
        runtime_url='http://local-canvas/private')
    item = service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})['items'][0]
    assert item['status']['code'] == expected
    assert item['version'] == {'revision': 2, 'sha256': digest}
    assert item['details']['deliveryVerification'] == 'current_file_verified'
    assert item['details']['engineVerification'] == 'not_checked'
    assert item['details']['publishEligibility'] == 'unverified'
    assert 'actorId' not in item['details']['project']['reviews'][0]
    assert 'cookie' not in str(item) and 'runtime_url' not in str(item) and 'local-canvas' not in str(item)


@pytest.mark.parametrize('mutation,expected', [('revision', 'stale_delivery'), ('file', 'delivery_unavailable')])
def test_historical_human_review_cannot_hide_stale_or_missing_delivery(host, mutation, expected):
    video = host._outputs_dir() / 'video/final.mp4'
    video.parent.mkdir()
    video.write_bytes(b'fixture video')
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    _, project = workshop(host, revision=3 if mutation == 'revision' else 2, stage='approved',
        deliverables=[{'id': 'b' * 32, 'revision': 2, 'sha256': digest, 'path': 'video/final.mp4', 'engine_revision': 4}],
        reviews=[{'id': 'c' * 32, 'kind': 'human', 'decision': 'approved', 'revision': 2,
                  'artifact_id': 'b' * 32, 'artifact_sha256': digest}])
    if mutation == 'file':
        video.unlink()
    item = service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})['items'][0]
    assert item['status']['code'] == expected
    assert item['details']['project']['reviews'][0]['kind'] == 'human'
    assert item['details']['publishEligibility'] == 'unverified'


@pytest.mark.parametrize('kind', ['idea', 'draft', 'workshop'])
def test_missing_or_deleted_object_is_404(host, kind):
    with pytest.raises(HTTPException) as caught:
        service(host).query({'action': 'inspect', 'kind': kind, 'id': 'f' * 32})
    assert caught.value.status_code == 404


def test_unknown_source_state_does_not_become_done(host):
    ideas(host, [{'id': 'one', 'title': 'Legacy', 'status': 'mystery'}])
    idea = service(host).query({'action': 'list', 'kind': 'idea'})['items'][0]
    assert idea['status']['source'] == 'unknown' and idea['status']['code'] == 'unknown'
    _, project = workshop(host, stage='future-stage')
    item = service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})['items'][0]
    assert item['status']['source'] == 'unknown' and item['status']['code'] == 'unknown'


def test_workshop_dto_does_not_expose_legacy_filesystem_paths(host):
    _, project = workshop(host, deliverables=[{'id': 'a' * 32, 'revision': 0,
        'sha256': 'b' * 64, 'path': '/private/host/credential-video.mp4'}])
    item = service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})['items'][0]
    assert '/private/host' not in str(item)
    assert 'path' not in item['details']['project']['deliverables'][0]


@pytest.mark.parametrize('reported', ['failed', 'succeeded'])
def test_unconfirmed_render_submission_cannot_claim_render_success_or_failure(host, reported):
    _, project = workshop(host, renders=[{'id': 'a' * 32, 'status': reported, 'content_revision': 1,
        'submission_state': 'unconfirmed', 'task_id': 'uncertain-task'}])
    item = service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})['items'][0]
    assert item['status']['code'] == 'render_submission_unconfirmed'


def test_legacy_current_artifacts_and_unpresented_files_are_not_new_draft_records(host):
    metadata = draft(host)
    directory = host._chat_artifact_store().directory
    with sqlite3.connect(directory / 'chat_artifacts.sqlite3') as connection:
        legacy = {**metadata, 'storageMode': 'legacy-current'}
        connection.execute('UPDATE artifacts SET payload=? WHERE id=?', (json.dumps(legacy), metadata['artifactId']))
    (host._outputs_dir() / 'content/unpresented.md').write_text('ordinary file, no delivery record')
    assert service(host).query({'action': 'list', 'kind': 'draft'})['items'] == []
    with pytest.raises(HTTPException) as caught:
        service(host).query({'action': 'inspect', 'kind': 'draft', 'id': metadata['artifactId']})
    assert caught.value.status_code == 404


def test_ip_shared_ideas_and_workshop_keep_current_query_actor_without_inventing_authorship(host):
    ideas(host, [{'id': 'one', 'title': 'Shared in IP', 'status': 'pending'}])
    _, project = workshop(host)
    with actor_context({'id': 'actor-two'}):
        for kind, identifier in [('idea', 'one'), ('workshop', project['id'])]:
            item = service(host).query({'action': 'inspect', 'kind': kind, 'id': identifier})['items'][0]
            assert item['actorId'] == 'actor-two' and item['projectId'] == 'ip-one'
    with project_context('ip-two', host.PROJECT_ROOT):
        with pytest.raises(HTTPException) as caught:
            service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})
        assert caught.value.status_code == 404


@pytest.mark.parametrize('field', ['renders', 'reviews', 'deliverables', 'publications'])
def test_corrupt_nested_workshop_catalog_is_an_explicit_source_error(host, field):
    _, project = workshop(host, **{field: None})
    with pytest.raises(HTTPException) as caught:
        service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})
    assert caught.value.status_code == 503


def test_legacy_render_receipt_without_status_stays_unknown(host):
    _, project = workshop(host, renders=[{'id': 'a' * 32, 'task_id': 'legacy-task'}])
    item = service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})['items'][0]
    assert item['status']['code'] == 'unknown' and item['status']['source'] == 'unknown'


def test_workshop_chat_projects_only_safe_render_attempt_evidence(host):
    start = '2026-11-03T04:00:00+00:00'
    end = '2026-11-03T04:00:01+00:00'
    _, project = workshop(host, renders=[{'id': 'a' * 32, 'status': 'running', 'content_revision': 1,
        'engine_revision': 4, 'submission_state': 'confirmed', 'task_id': 'observed-task',
        'submission_attempts': [
            {'number': 1, 'status': 'unconfirmed', 'started_at': start, 'settled_at': end,
             'error_code': 'submission_interrupted', 'raw': 'secret-upstream', 'task_id': 'unverified'},
            {'number': 2, 'status': 'confirmed', 'started_at': end, 'settled_at': end,
             'task_id': 'observed-task', 'message': 'secret-upstream'},
        ]}])
    item = service(host).query({'action': 'inspect', 'kind': 'workshop', 'id': project['id']})['items'][0]
    assert item['details']['project']['renders'][0]['submission_attempts'] == [
        {'number': 1, 'status': 'unconfirmed', 'started_at': start, 'settled_at': end,
         'error_code': 'submission_interrupted'},
        {'number': 2, 'status': 'confirmed', 'started_at': end, 'settled_at': end,
         'task_id': 'observed-task'},
    ]
