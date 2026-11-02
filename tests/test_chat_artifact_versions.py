"""D08 immutable delivery revisions, isolated from live user artifact catalogs."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from easel.chat_artifacts import ArtifactError, ChatArtifactStore


@pytest.fixture
def versions(tmp_path):
    workspace = tmp_path / 'workspace'
    outputs = workspace / 'outputs'
    (outputs / 'demo').mkdir(parents=True)
    directory = tmp_path / 'private-user-ip'
    store = ChatArtifactStore(directory, outputs, workspace, 'default')
    return store, outputs, directory, workspace


def declare(store, call, *, turn='turn', description='report'):
    return store.present([{'path': 'outputs/demo/report.txt', 'description': description}],
                         session_id='session', turn_id=turn, call_id=call)[0]


def content(store, artifact):
    with store.open_content(artifact['artifactId']) as stream:
        return stream.read()


def test_new_present_snapshots_verified_bytes_before_source_changes_or_disappears(versions):
    store, outputs, _, _ = versions
    source = outputs / 'demo' / 'report.txt'
    source.write_bytes(b'version-one')
    first = declare(store, 'first')
    assert first['storageMode'] == 'snapshot'
    assert first['revision'] == 1
    assert first['sha256'] == hashlib.sha256(b'version-one').hexdigest()
    assert first['reviewStatus'] == 'unreviewed'
    source.write_bytes(b'version-two')
    assert content(store, first) == b'version-one'
    source.unlink()
    assert content(store, first) == b'version-one'


def test_changed_content_and_a_b_a_have_distinct_revisions_and_fixed_old_ids(versions):
    store, outputs, _, _ = versions
    source = outputs / 'demo' / 'report.txt'
    artifacts = []
    for number, data in enumerate((b'A', b'B', b'A'), 1):
        source.write_bytes(data)
        artifacts.append(declare(store, f'call-{number}'))
    assert [item['revision'] for item in artifacts] == [1, 2, 3]
    assert len({item['artifactId'] for item in artifacts}) == 3
    assert [content(store, item) for item in artifacts] == [b'A', b'B', b'A']
    assert artifacts[0]['sha256'] == artifacts[2]['sha256']


def test_same_turn_latest_identical_content_reuses_id_but_new_turn_keeps_delivery_context(versions):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'report.txt').write_bytes(b'unchanged')
    first = declare(store, 'first', description='old')
    repeated = declare(store, 'another-call', description='new')
    assert repeated['artifactId'] == first['artifactId']
    assert repeated['revision'] == first['revision'] == 1
    assert repeated['description'] == 'new'
    next_turn = declare(store, 'third-call', turn='next-turn')
    assert next_turn['artifactId'] != first['artifactId']
    assert next_turn['revision'] == 2
    assert next_turn['turnId'] == 'next-turn'


def test_committed_call_retry_never_resnapshots_later_source_bytes(versions):
    store, outputs, _, _ = versions
    source = outputs / 'demo' / 'report.txt'
    source.write_bytes(b'accepted')
    first = declare(store, 'same-call')
    source.write_bytes(b'later-source')
    retried = declare(store, 'same-call')
    assert retried == first
    assert content(store, retried) == b'accepted'
    assert len(store.for_turn('session', 'turn')) == 1
    source.unlink()
    assert declare(store, 'same-call') == first


def test_call_identity_cannot_be_rebound_to_different_arguments(versions):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'report.txt').write_bytes(b'accepted')
    declare(store, 'same-call', description='original')
    with pytest.raises(ArtifactError) as caught:
        declare(store, 'same-call', description='different')
    assert caught.value.status_code == 409


def seed_legacy(directory, payload):
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(directory / 'chat_artifacts.sqlite3') as connection:
        connection.execute('CREATE TABLE artifacts (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, '
            'turn_id TEXT NOT NULL, path TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL, '
            'UNIQUE(session_id,turn_id,path))')
        connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',
            (payload['artifactId'], payload['sessionId'], payload['turnId'], payload['path'], 0, json.dumps(payload)))


def test_old_ids_migrate_as_explicit_legacy_current_without_fabricated_hashes(tmp_path):
    workspace = tmp_path / 'workspace'
    outputs = workspace / 'outputs'
    (outputs / 'demo').mkdir(parents=True)
    source = outputs / 'demo' / 'report.txt'
    source.write_bytes(b'current')
    directory = tmp_path / 'private'
    old = {'artifactId': '1' * 32, 'projectId': 'default', 'sessionId': 'session', 'turnId': 'turn',
           'path': 'demo/report.txt', 'name': 'report.txt', 'mime': 'text/plain', 'size': 7,
           'status': 'available', 'createdAt': 1, 'url': '/api/chat/artifacts/' + '1' * 32 + '/content'}
    seed_legacy(directory, old)
    store = ChatArtifactStore(directory, outputs, workspace, 'default')
    migrated = store.get(old['artifactId'])
    assert migrated['artifactId'] == old['artifactId']
    assert migrated['storageMode'] == 'legacy-current'
    assert migrated['revision'] is None
    assert migrated['sha256'] is None
    assert migrated['bundleSha256'] is None
    source.write_bytes(b'new-current')
    assert content(store, migrated) == b'new-current'
    fresh = declare(store, 'new-call')
    assert fresh['storageMode'] == 'snapshot'
    assert fresh['artifactId'] != old['artifactId']
    assert store.get(old['artifactId'])['storageMode'] == 'legacy-current'
    assert len(store.for_turn('session', 'turn')) == 2


def test_corrupt_or_missing_snapshot_never_falls_back_to_current_source(versions):
    store, outputs, directory, _ = versions
    source = outputs / 'demo' / 'report.txt'
    source.write_bytes(b'original')
    first = declare(store, 'first')
    source.write_bytes(b'current-must-not-leak')
    blob = directory / 'artifact_blobs' / first['sha256']
    blob.chmod(0o600)
    blob.write_bytes(b'corrupt')
    with pytest.raises(ArtifactError):
        store.open_content(first['artifactId'])


def test_html_root_and_direct_assets_are_snapshotted_before_first_preview(versions):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'style.css').write_bytes(b'body{color:blue}')
    (outputs / 'demo' / 'app.js').write_bytes(b'window.version=1')
    html = b'<link rel="stylesheet" href="style.css"><script src="app.js"></script>'
    (outputs / 'demo' / 'page.html').write_bytes(html)
    artifact = store.present([{'path': 'outputs/demo/page.html'}], session_id='session', turn_id='turn', call_id='html')[0]
    assert len(artifact['bundleSha256']) == 64
    (outputs / 'demo' / 'style.css').write_bytes(b'body{color:red}')
    (outputs / 'demo' / 'app.js').unlink()
    (outputs / 'demo' / 'page.html').unlink()
    assert content(store, artifact) == html
    assert store.read_related(artifact['artifactId'], 'style.css')[0] == b'body{color:blue}'
    assert store.read_related(artifact['artifactId'], 'app.js')[0] == b'window.version=1'
    (outputs / 'demo' / 'late.js').write_bytes(b'never captured')
    with pytest.raises(ArtifactError) as caught:
        store.read_related(artifact['artifactId'], 'late.js')
    assert caught.value.status_code == 404


def test_html_dependency_only_change_creates_new_revision_and_bundle_hash(versions):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'style.css').write_bytes(b'old')
    (outputs / 'demo' / 'page.html').write_bytes(b'<link rel="stylesheet" href="style.css">')
    first = store.present([{'path': 'outputs/demo/page.html'}], session_id='session', turn_id='turn', call_id='html-1')[0]
    (outputs / 'demo' / 'style.css').write_bytes(b'new')
    second = store.present([{'path': 'outputs/demo/page.html'}], session_id='session', turn_id='turn', call_id='html-2')[0]
    assert first['sha256'] == second['sha256']
    assert first['bundleSha256'] != second['bundleSha256']
    assert [first['revision'], second['revision']] == [1, 2]
    assert store.read_related(first['artifactId'], 'style.css')[0] == b'old'
    assert store.read_related(second['artifactId'], 'style.css')[0] == b'new'


def test_snapshot_manifest_is_finite_without_guessing_modules_or_recursive_css_assets(versions):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'style.css').write_bytes(b'@import "nested.css";')
    (outputs / 'demo' / 'nested.css').write_bytes(b'not direct')
    (outputs / 'demo' / 'module.js').write_bytes(b'not classic')
    (outputs / 'demo' / 'page.html').write_bytes(b'<link rel="stylesheet" href="style.css"><script type="module" src="module.js"></script>')
    artifact = store.present([{'path': 'outputs/demo/page.html'}], session_id='session', turn_id='turn', call_id='html')[0]
    assert store.read_related(artifact['artifactId'], 'style.css')[0] == b'@import "nested.css";'
    for path in ('nested.css', 'module.js'):
        with pytest.raises(ArtifactError) as caught:
            store.read_related(artifact['artifactId'], path)
        assert caught.value.status_code == 404


def test_unsafe_html_dependency_aborts_complete_batch_without_partial_version(versions):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'report.txt').write_bytes(b'valid')
    (outputs / 'demo' / 'page.html').write_bytes(b'<script src="../_login/private.js"></script>')
    with pytest.raises(ArtifactError):
        store.present([{'path': 'outputs/demo/report.txt'}, {'path': 'outputs/demo/page.html'}],
                      session_id='session', turn_id='turn', call_id='batch')
    assert store.for_turn('session', 'turn') == []


def test_ready_schema_read_does_not_upgrade_to_writer_lock(versions):
    from contextlib import closing
    store, outputs, directory, workspace = versions
    (outputs / 'demo' / 'report.txt').write_bytes(b'original')
    first = declare(store, 'first')
    with closing(sqlite3.connect(store.path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        pending = {**first, 'description': 'uncommitted'}
        writer.execute('UPDATE artifacts SET payload=? WHERE id=?', (json.dumps(pending), first['artifactId']))
        reopened = ChatArtifactStore(directory, outputs, workspace, 'default')
        assert reopened.get(first['artifactId'])['description'] == 'report'
        assert content(reopened, first) == b'original'
        assert writer.in_transaction
        writer.rollback()


def test_cancelled_capture_never_commits_partial_revision(versions, monkeypatch):
    import threading
    from easel.artifact_blobs import ArtifactBlobStore
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'report.txt').write_bytes(b'content')
    cancelled = threading.Event()
    original = ArtifactBlobStore.capture
    def capture(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        cancelled.set()
        return value
    monkeypatch.setattr(ArtifactBlobStore, 'capture', capture)
    with pytest.raises(ArtifactError) as caught:
        store.present([{'path': 'outputs/demo/report.txt'}], session_id='session', turn_id='turn',
                      call_id='cancelled', cancelled=cancelled.is_set)
    assert caught.value.status_code == 499
    assert store.for_turn('session', 'turn') == []


def test_native_cancel_waits_for_capture_worker_and_never_emits_success(tmp_path, monkeypatch):
    import asyncio
    import threading
    from easel.artifact_blobs import ArtifactBlobStore
    from easel.request_identity import ActorMap
    from web import app as web
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    (tmp_path / 'outputs' / 'demo').mkdir(parents=True)
    (tmp_path / 'outputs' / 'demo' / 'report.txt').write_bytes(b'content')
    monkeypatch.setattr(web, '_RUNNING_CHAT', ActorMap())
    monkeypatch.setattr(web, '_session_locks', ActorMap())
    monkeypatch.setattr(web, '_chat_backend', lambda _: 'codex')
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    capture_original = ArtifactBlobStore.capture
    present_original = ChatArtifactStore.present
    def capture(self, *args, **kwargs):
        started.set()
        assert release.wait(3)
        return capture_original(self, *args, **kwargs)
    def present(self, *args, **kwargs):
        try:
            return present_original(self, *args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(ArtifactBlobStore, 'capture', capture)
    monkeypatch.setattr(ChatArtifactStore, 'present', present)
    class Adapter:
        async def run(self, request, emit, ask, bind):
            await request.present([{'path': 'outputs/demo/report.txt'}], 'cancelled')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    events = []
    async def scenario():
        task = asyncio.create_task(web._run_harness_chat(web.ChatRequest(message='run', sessionId='s', turnId='t'),
            's', 'run', 'codex', {'models': {}, 'sandbox': 'read-only'}, lambda kind, value: events.append((kind, value))))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            await task
            assert finished.is_set(), 'Cancelled callback returned before its worker released file resources'
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 3)
    asyncio.run(scenario())
    assert not [value for kind, value in events if kind == 'agent_event' and value.get('kind') == 'artifact']
    assert ChatArtifactStore(web._sessions_dir(), web._outputs_dir(), web._workspace_root(), 'default').for_turn('s', 't') == []


def test_encoded_html_resource_urls_decode_once_and_remain_pinned(versions):
    store, outputs, _, _ = versions
    files = {'image name.png': b'image-one', '中文.png': b'image-two',
             'percent%20name.js': b'window.value=1', 'style#hash.css': b'body{color:blue}'}
    for name, data in files.items():
        (outputs / 'demo' / name).write_bytes(data)
    html = ('<img src="image%20name.png?x=1#f"><img src="%E4%B8%AD%E6%96%87.png">'
            '<script src="percent%2520name.js"></script><link rel="stylesheet" href="style%23hash.css">').encode()
    (outputs / 'demo' / 'page.html').write_bytes(html)
    artifact = store.present([{'path': 'outputs/demo/page.html'}], session_id='session', turn_id='turn', call_id='html')[0]
    for name, expected in files.items():
        (outputs / 'demo' / name).unlink()
        assert store.read_related(artifact['artifactId'], name)[0] == expected
    with pytest.raises(ArtifactError) as caught:
        store.read_related(artifact['artifactId'], 'image%20name.png')
    assert caught.value.status_code == 404


def test_encoded_private_dependency_cannot_escape_safe_source_policy(versions):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'page.html').write_bytes(b'<script src="..%2F_login%2Fprivate.js"></script>')
    with pytest.raises(ArtifactError) as caught:
        store.present([{'path': 'outputs/demo/page.html'}], session_id='session', turn_id='turn', call_id='bad')
    assert caught.value.status_code == 403
    assert store.for_turn('session', 'turn') == []


@pytest.mark.parametrize('case', ['asset-count', 'total-bytes'])
def test_html_snapshot_limits_apply_before_any_version_is_committed(versions, case):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'style.css').write_bytes(b'x' if case == 'asset-count' else b'x' * (4 * 1024 * 1024))
    count = 65 if case == 'asset-count' else 8
    (outputs / 'demo' / 'page.html').write_text(''.join(f'<link rel="stylesheet" href="style.css?v={i}">' for i in range(count)))
    with pytest.raises(ArtifactError) as caught:
        store.present([{'path': 'outputs/demo/page.html'}], session_id='session', turn_id='turn', call_id=case)
    assert caught.value.status_code == 413
    assert store.for_turn('session', 'turn') == []


def test_failed_batch_rolls_back_revisions_resources_and_receipts(versions):
    store, outputs, _, _ = versions
    source = outputs / 'demo' / 'report.txt'
    source.write_bytes(b'original')
    first = declare(store, 'first')
    source.write_bytes(b'aborted')
    (outputs / 'demo' / 'blocked.txt').write_bytes(b'blocked')
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TRIGGER fail_batch BEFORE INSERT ON artifacts WHEN NEW.path='demo/blocked.txt' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    args = [{'path': 'outputs/demo/report.txt'}, {'path': 'outputs/demo/blocked.txt'}]
    with pytest.raises(sqlite3.IntegrityError):
        store.present(args, session_id='session', turn_id='turn', call_id='batch')
    assert len(store.for_turn('session', 'turn')) == 1
    assert content(store, first) == b'original'
    with sqlite3.connect(store.path) as connection:
        connection.execute('DROP TRIGGER fail_batch')
    source.write_bytes(b'retry-accepted')
    retried = store.present(args, session_id='session', turn_id='turn', call_id='batch')
    assert retried[0]['revision'] == 2
    assert content(store, retried[0]) == b'retry-accepted'


def test_unknown_storage_mode_never_downgrades_to_current_source(versions):
    store, outputs, _, _ = versions
    source = outputs / 'demo' / 'report.txt'
    source.write_bytes(b'original')
    artifact = declare(store, 'first')
    source.write_bytes(b'current-must-not-leak')
    with sqlite3.connect(store.path) as connection:
        connection.execute('UPDATE artifacts SET payload=? WHERE id=?',
            (json.dumps({**artifact, 'storageMode': 'unsupported-snapshot'}), artifact['artifactId']))
    with pytest.raises(ArtifactError):
        store.open_content(artifact['artifactId'])


def test_resource_corruption_marks_snapshot_bundle_unavailable_without_source_fallback(versions):
    store, outputs, directory, _ = versions
    (outputs / 'demo' / 'style.css').write_bytes(b'original-style')
    (outputs / 'demo' / 'page.html').write_bytes(b'<link rel="stylesheet" href="style.css">')
    artifact = store.present([{'path': 'outputs/demo/page.html'}], session_id='session', turn_id='turn', call_id='html')[0]
    store.verify_available(artifact['artifactId'])
    digest = hashlib.sha256(b'original-style').hexdigest()
    blob = directory / 'artifact_blobs' / digest
    blob.chmod(0o600)
    blob.write_bytes(b'corrupt')
    (outputs / 'demo' / 'style.css').write_bytes(b'current-must-not-leak')
    with pytest.raises(ArtifactError):
        store.verify_available(artifact['artifactId'])
    with pytest.raises(ArtifactError):
        store.read_related(artifact['artifactId'], 'style.css')


@pytest.mark.parametrize('mutation', ['remove', 'substitute'])
def test_stored_manifest_integrity_is_bound_to_bundle_hash(versions, mutation):
    store, outputs, _, _ = versions
    (outputs / 'demo' / 'style.css').write_bytes(b'original-style')
    (outputs / 'demo' / 'page.html').write_bytes(b'<link rel="stylesheet" href="style.css">')
    args = [{'path': 'outputs/demo/page.html'}]
    first = store.present(args, session_id='session', turn_id='turn', call_id='first')[0]
    (outputs / 'demo' / 'style.css').write_bytes(b'other-style')
    second = store.present(args, session_id='session', turn_id='turn', call_id='second')[0]
    with sqlite3.connect(store.path) as connection:
        if mutation == 'remove':
            connection.execute('DELETE FROM artifact_resources WHERE artifact_id=?', (first['artifactId'],))
        else:
            wrong = connection.execute('SELECT payload FROM artifact_resources WHERE artifact_id=?',
                                       (second['artifactId'],)).fetchone()[0]
            connection.execute('UPDATE artifact_resources SET payload=? WHERE artifact_id=?', (wrong, first['artifactId']))
    with pytest.raises(ArtifactError):
        store.verify_available(first['artifactId'])
    with pytest.raises(ArtifactError):
        store.read_related(first['artifactId'], 'style.css')
