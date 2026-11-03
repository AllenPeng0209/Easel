"""B05 compares only authorized, immutable text artifact snapshots."""
import pytest

from easel.artifact_blobs import ArtifactBlobStore
from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

pytestmark = pytest.mark.access_integration
REPORT = 'topic/report.txt'


def declare(environment, data, *, path=REPORT, session='owner-session', turn='version'):
    root, owner, *_ = environment
    source = root / 'outputs' / path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(data)
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        return web._chat_artifact_store().present([{'path': 'outputs/' + path}], session_id=session,
                                                  turn_id=turn, call_id=turn)[0]


def compare(client, after, before, *, prefix='/api/chat/artifacts'):
    return client.get(f"{prefix}/{after['artifactId']}/compare", params={'beforeId': before['artifactId']})


def version(metadata):
    return {name: metadata[name] for name in ('artifactId', 'revision', 'sha256')}


def test_compare_returns_exact_fixed_versions_and_patch_without_current_file(artifact_http_environment):
    root, owner, members, client, _ = artifact_http_environment
    before = declare(artifact_http_environment, b'keep\nold\n', turn='first')
    after = declare(artifact_http_environment, b'keep\nnew\n', turn='second')
    (root / 'outputs' / REPORT).unlink()
    result = compare(owner, after, before)
    assert result.status_code == 200, result.text
    assert result.json() == {'path': REPORT, 'before': version(before), 'after': version(after),
        'identical': False, 'diff': '--- a/topic/report.txt\n+++ b/topic/report.txt\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\n'}
    assert compare(members['operator'], after, before).status_code == 404
    assert compare(members['viewer'], after, before).status_code == 403
    assert compare(client(), after, before).status_code == 401
    project = owner.post('/api/projects', json={'name': 'Other comparison IP'}).json()['id']
    assert compare(owner, after, before, prefix=f'/api/projects/{project}/chat/artifacts').status_code == 404


@pytest.mark.parametrize('before_bytes,after_bytes,identical,expects_hunk', [
    (b'same\n', b'same\n', True, False),
    (b'same\r\n', b'same\n', False, False),
    (b'same', b'same\n', False, True),
])
def test_byte_identity_is_distinct_from_text_hunks(
        artifact_http_environment, before_bytes, after_bytes, identical, expects_hunk):
    _, owner, *_ = artifact_http_environment
    before = declare(artifact_http_environment, before_bytes, turn='first')
    after = declare(artifact_http_environment, after_bytes, turn='second')
    response = compare(owner, after, before)
    assert response.status_code == 200, response.text
    assert response.json()['identical'] is identical
    diff = response.json()['diff']
    if expects_hunk:
        assert '-same\n\\ No newline at end of file\n+same\n' in diff
    else:
        assert diff == ''


def test_compare_refuses_missing_legacy_and_wrong_lineage(artifact_http_environment):
    _, owner, _, _, legacy = artifact_http_environment
    after = declare(artifact_http_environment, b'current\n')
    another_session = declare(artifact_http_environment, b'not same', session='foreign-session', turn='foreign')
    another_path = declare(artifact_http_environment, b'not same', path='topic/other.txt', turn='other')
    assert compare(owner, after, {'artifactId': 'f' * 32}).status_code == 404
    assert compare(owner, after, legacy[0]).status_code == 409
    assert compare(owner, after, another_session).status_code == 409
    assert compare(owner, after, another_path).status_code == 409
    assert owner.get(f"/api/chat/artifacts/{after['artifactId']}/compare").status_code == 422


@pytest.mark.parametrize('path,data', [('topic/blob.txt', b'with\x00nul'),
                                       ('topic/invalid.txt', b'\xff'),
                                       ('topic/blob.png', b'looks like text'),
                                       ('topic/page.html', b'<p>HTML bundle</p>')])
def test_compare_refuses_nonplain_or_non_utf8_content(artifact_http_environment, path, data):
    _, owner, *_ = artifact_http_environment
    first = declare(artifact_http_environment, data, path=path, turn='first')
    second = declare(artifact_http_environment, data, path=path, turn='second')
    assert compare(owner, second, first).status_code == 415


def test_compare_rejects_corrupt_blob_even_if_current_file_exists(artifact_http_environment):
    root, owner, *_ = artifact_http_environment
    before = declare(artifact_http_environment, b'old version', turn='first')
    after = declare(artifact_http_environment, b'new version', turn='second')
    path = root / 'outputs' / '_sessions' / 'artifact_blobs' / before['sha256']
    path.chmod(0o600)
    path.write_bytes(b'old corrupt')
    assert compare(owner, after, before).status_code == 409


def test_compare_checks_metadata_bounds_before_opening_any_blob(artifact_http_environment, monkeypatch):
    _, owner, *_ = artifact_http_environment
    before = declare(artifact_http_environment, b'x' * (256 * 1024 + 1), turn='first')
    after = declare(artifact_http_environment, b'small', turn='second')

    def unexpected(*_):
        pytest.fail('Oversized metadata must not trigger full hash reads')

    monkeypatch.setattr(ArtifactBlobStore, 'open_blob', unexpected)
    assert compare(owner, after, before).status_code == 413


def test_compare_bounds_quadratic_work_even_for_small_files(artifact_http_environment):
    _, owner, *_ = artifact_http_environment
    before = declare(artifact_http_environment, b'old\n' * 1800, turn='first')
    after = declare(artifact_http_environment, b'new\n' * 1800, turn='second')
    assert compare(owner, after, before).status_code == 413


def test_compare_checks_bytes_read_after_blob_reader_verified_the_handle(artifact_http_environment, monkeypatch):
    root, owner, *_ = artifact_http_environment
    before = declare(artifact_http_environment, b'before', turn='first')
    after = declare(artifact_http_environment, b'after', turn='second')
    original = ArtifactBlobStore.open_blob

    def mutate(store, digest, size):
        stream = original(store, digest, size)
        if digest == before['sha256']:
            path = root / 'outputs' / '_sessions' / 'artifact_blobs' / digest
            path.chmod(0o600)
            path.write_bytes(b'unsafe')
        return stream

    monkeypatch.setattr(ArtifactBlobStore, 'open_blob', mutate)
    assert compare(owner, after, before).status_code == 409


def test_compare_rechecks_role_after_verified_blob_read(artifact_http_environment, monkeypatch):
    import secrets
    from easel.access import AccessStore

    root, owner, *_ = artifact_http_environment
    before = declare(artifact_http_environment, b'before', turn='first')
    after = declare(artifact_http_environment, b'after', turn='second')
    actor = owner.get('/api/auth/me').json()
    access = AccessStore(root)
    replacement = access.create_user('compare-replacement', secrets.token_urlsafe(24), 'Replacement')
    access.set_member('default', replacement['id'], 'owner')
    original = ArtifactBlobStore.open_blob
    handles = []

    def revoke(store, digest, size):
        stream = original(store, digest, size)
        handles.append(stream)
        if len(handles) == 1:
            access.set_member('default', actor['id'], 'viewer')
        return stream

    monkeypatch.setattr(ArtifactBlobStore, 'open_blob', revoke)
    assert compare(owner, after, before).status_code == 403
    assert handles and all(stream.closed for stream in handles)
