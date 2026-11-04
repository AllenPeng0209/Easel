"""D08 public routes retain version identity across reads, ranges and ZIP preview."""
import asyncio
import threading
import zipfile
import pytest

from tests.test_chat_artifacts import artifact_http_environment
from easel.chat_artifacts import ChatArtifactStore
from easel.projects import project_context
from easel.request_identity import actor_context
import web.app as web

pytestmark = pytest.mark.access_integration


@pytest.mark.parametrize('suffix', ['', '/content', '/related?path=image.png', '/archive'])
def test_corrupt_snapshot_metadata_returns_conflict_without_current_source(artifact_http_environment, monkeypatch, suffix):
    _, owner, _, _, files = artifact_http_environment
    from easel.chat_artifacts import ArtifactError
    def corrupt(*args):
        raise ArtifactError('固定版本元数据无效', 409)
    monkeypatch.setattr(ChatArtifactStore, 'get', corrupt)
    response = owner.get('/api/chat/artifacts/' + files[0]['artifactId'] + suffix)
    assert response.status_code == 409
    assert '固定版本元数据无效' in response.text


def declare(environment, path, call='next'):
    root, owner, *_ = environment
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        store = ChatArtifactStore(web._sessions_dir(), web._outputs_dir(), web._workspace_root(), 'default')
        return store.present([{'path': path}], session_id='owner-session', turn_id=call, call_id=call)[0]


def test_version_history_never_replaces_the_old_content_or_range(artifact_http_environment):
    root, owner, members, client, files = artifact_http_environment
    original = declare(artifact_http_environment, 'outputs/topic/report.txt', 'original')
    (root / 'outputs/topic/report.txt').write_bytes(b'version-two')
    updated = declare(artifact_http_environment, 'outputs/topic/report.txt')
    prefix = '/api/chat/artifacts/' + original['artifactId']
    response = owner.get(prefix + '/versions')
    assert response.status_code == 200, response.text
    history = response.json()['versions']
    assert [file['revision'] for file in history] == [2, 1, None]
    assert history[-1]['storageMode'] == 'legacy-current'
    assert history[0]['artifactId'] == updated['artifactId']
    assert owner.get(prefix + '/content').content == b'owned-result'
    selected = owner.get(prefix + '/content', headers={'Range': 'bytes=1-4'})
    assert selected.status_code == 206
    assert selected.content == b'wned'
    assert members['operator'].get(prefix + '/versions').status_code == 404
    assert members['viewer'].get(prefix + '/versions').status_code == 403
    assert client().get(prefix + '/versions').status_code == 401


def test_zip_member_and_html_resource_reads_use_the_original_archive(artifact_http_environment):
    root, owner, members, client, _ = artifact_http_environment
    path = root / 'outputs/topic/bundle.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('a.png', b'ORIGINAL')
        archive.writestr('page.html', '<img src="a.png">')
        archive.writestr('notes.txt', 'download only')
    artifact = declare(artifact_http_environment, 'outputs/topic/bundle.zip', 'zip')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('a.png', b'REPLACEMENT')
    prefix = '/api/chat/artifacts/' + artifact['artifactId']
    response = owner.get(prefix + '/archive')
    assert response.status_code == 200, response.text
    files = response.json()['files']
    assert [item['index'] for item in files] == [0, 1, 2]
    assert files[2]['kind'] == 'file'
    assert files[0]['url'] == prefix + '/archive/0/content'
    content = owner.get(files[0]['url'], headers={'Range': 'bytes=1-3'})
    assert content.status_code == 206
    assert content.content == b'RIG'
    head = owner.head(files[0]['url'])
    assert head.status_code == 200 and head.content == b''
    assert head.headers['content-length'] == '8'
    resource = owner.get(prefix + '/archive/1/related', params={'path': 'a.png'})
    assert resource.status_code == 200 and resource.content == b'ORIGINAL'
    assert resource.headers['x-content-type-options'] == 'nosniff'
    assert owner.get(prefix + '/archive/1/related', params={'path': '../_login/private.png'}).status_code in (403, 404)
    assert members['operator'].get(prefix + '/archive').status_code == 404
    assert members['viewer'].get(files[0]['url']).status_code == 403
    assert client().get(files[0]['url']).status_code == 401


def test_revocation_during_snapshot_verification_closes_the_pending_download(artifact_http_environment, monkeypatch):
    root, owner, _, _, _ = artifact_http_environment
    artifact = declare(artifact_http_environment, 'outputs/topic/report.txt', 'revoke-snapshot')
    from easel.access import AccessStore
    user = owner.get('/api/auth/me').json()
    original = ChatArtifactStore.open_content
    opened = []
    def revoke_after_verification(store, *args, **kwargs):
        stream = original(store, *args, **kwargs)
        opened.append(stream)
        access = AccessStore(root)
        other = access.create_user('replacement-owner', __import__('secrets').token_urlsafe(24), 'Replacement')
        access.set_member('default', other['id'], 'owner')
        access.set_member('default', user['id'], 'viewer')
        return stream
    monkeypatch.setattr(ChatArtifactStore, 'open_content', revoke_after_verification)
    response = owner.get('/api/chat/artifacts/' + artifact['artifactId'] + '/content')
    assert response.status_code == 403
    assert opened and all(stream.closed for stream in opened)


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout=2)


def request_for(method='GET'):
    from starlette.requests import Request
    return Request({'type':'http', 'method':method, 'path':'/', 'headers':[]})


@pytest.mark.parametrize('method', ['GET', 'HEAD'])
def test_cancelled_snapshot_open_closes_the_late_verified_descriptor(artifact_http_environment, monkeypatch, method):
    root, owner, *_ = artifact_http_environment
    artifact = declare(artifact_http_environment, 'outputs/topic/report.txt', 'cancel-open')
    user = owner.get('/api/auth/me').json()
    original = ChatArtifactStore.open_content
    opened = []
    started, release = threading.Event(), threading.Event()
    def blocked_open(store, *args, **kwargs):
        stream = original(store, *args, **kwargs)
        opened.append(stream)
        started.set()
        assert release.wait(3)
        return stream
    monkeypatch.setattr(ChatArtifactStore, 'open_content', blocked_open)
    async def scenario():
        with actor_context(user), project_context('default', root):
            task = asyncio.create_task(web.api_chat_artifact_content(artifact['artifactId'], request_for(method)))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                release.set()
                await wait_until(lambda: opened and opened[0].closed)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                for stream in opened:
                    stream.close()
    asyncio.run(scenario())


def zip_artifact(environment, call):
    root, *_ = environment
    with zipfile.ZipFile(root / 'outputs/topic/cancel.zip', 'w') as archive:
        archive.writestr('clip.mp4', b'fixed-media-version')
    return declare(environment, 'outputs/topic/cancel.zip', call)


def test_cancelled_archive_construction_closes_the_late_reader_and_blob(artifact_http_environment, monkeypatch):
    root, owner, *_ = artifact_http_environment
    artifact = zip_artifact(artifact_http_environment, 'cancel-archive')
    user = owner.get('/api/auth/me').json()
    original = web.ArchiveSnapshotReader
    readers = []
    started, release = threading.Event(), threading.Event()
    def blocked_reader(blob):
        reader = original(blob)
        readers.append(reader)
        started.set()
        assert release.wait(3)
        return reader
    monkeypatch.setattr(web, 'ArchiveSnapshotReader', blocked_reader)
    async def scenario():
        with actor_context(user), project_context('default', root):
            task = asyncio.create_task(web.api_chat_artifact_archive(artifact['artifactId']))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                release.set()
                await wait_until(lambda: readers and readers[0]._closed and readers[0]._blob.closed)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                for reader in readers:
                    reader.close()
    asyncio.run(scenario())


def test_cancelled_member_open_owns_reader_until_late_stream_is_closed(artifact_http_environment, monkeypatch):
    root, owner, *_ = artifact_http_environment
    artifact = zip_artifact(artifact_http_environment, 'cancel-member')
    user = owner.get('/api/auth/me').json()
    reader_class = web.ArchiveSnapshotReader
    original = reader_class._open
    opened, readers = [], []
    started, release = threading.Event(), threading.Event()
    calls = 0
    def blocked_member(reader, info):
        nonlocal calls
        stream = original(reader, info)
        calls += 1
        if calls == 2:  # CRC verification finishes before the returned stream is registered.
            opened.append(stream)
            readers.append(reader)
            started.set()
            assert release.wait(3)
        return stream
    monkeypatch.setattr(reader_class, '_open', blocked_member)
    async def scenario():
        with actor_context(user), project_context('default', root):
            task = asyncio.create_task(web.api_chat_artifact_archive_content(artifact['artifactId'], 0, request_for()))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not readers[0]._closed, 'Reader closed concurrently with its pending member open'
                release.set()
                await wait_until(lambda: opened[0].closed and readers[0]._closed and readers[0]._blob.closed)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                for stream in opened:
                    stream.close()
                for reader in readers:
                    reader.close()
    asyncio.run(scenario())


def test_archive_constructor_failure_closes_its_verified_blob(artifact_http_environment, monkeypatch):
    root, owner, *_ = artifact_http_environment
    artifact = zip_artifact(artifact_http_environment, 'failed-reader')
    user = owner.get('/api/auth/me').json()
    original = ChatArtifactStore.open_content
    opened = []
    def tracked_open(store, *args, **kwargs):
        stream = original(store, *args, **kwargs)
        opened.append(stream)
        return stream
    def broken_reader(blob):
        raise RuntimeError('injected constructor failure')
    monkeypatch.setattr(ChatArtifactStore, 'open_content', tracked_open)
    monkeypatch.setattr(web, 'ArchiveSnapshotReader', broken_reader)
    async def scenario():
        with actor_context(user), project_context('default', root):
            with pytest.raises(RuntimeError, match='injected constructor failure'):
                await web._artifact_archive_reader(artifact['artifactId'])
        try:
            assert opened and opened[0].closed
        finally:
            for stream in opened:
                stream.close()
    asyncio.run(scenario())
