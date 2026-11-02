"""Publication adapters stage approved D08 snapshots, never mutable source files."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest

from easel.chat_artifacts import ArtifactError, ChatArtifactStore


@pytest.fixture
def media_module():
    try:
        return importlib.import_module('easel.publication_media')
    except ModuleNotFoundError as error:
        if error.name == 'easel.publication_media':
            pytest.fail('Publication media snapshot adapter is not implemented')
        raise


@pytest.fixture
def media_store(tmp_path):
    workspace = tmp_path / 'workspace'
    outputs = workspace / 'outputs'
    (outputs / 'album').mkdir(parents=True)
    (outputs / 'album' / 'cover.png').write_bytes(b'approved-cover')
    (outputs / 'album' / 'clip.mp4').write_bytes(b'approved-video')
    return ChatArtifactStore(tmp_path / 'actor-ip', outputs, workspace, 'default')


def capture(module, store, paths=None, artifact_ids=None, call='publication-prepare'):
    return module.capture_publication_media(store, paths=paths or [], artifact_ids=artifact_ids or [],
        session_id='publication-session', turn_id='prepare-turn', call_id=call)


def test_source_overwrite_and_deletion_cannot_change_staged_media(media_module, media_store, tmp_path):
    media = capture(media_module, media_store, ['album/cover.png', 'outputs/album/clip.mp4'])
    (media_store.outputs / 'album/cover.png').write_bytes(b'later-unapproved-cover')
    (media_store.outputs / 'album/clip.mp4').unlink()
    paths = media_module.stage_publication_media(media_store, media, tmp_path / 'private-task')
    assert [Path(path).read_bytes() for path in paths] == [b'approved-cover', b'approved-video']
    assert [Path(path).suffix for path in paths] == ['.png', '.mp4']
    assert all(Path(path).is_absolute() for path in paths)
    for entry, path in zip(media, paths):
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == entry['sha256']
        assert entry['revision'] == 1 and entry['bundleSha256'] is None


def test_more_than_eight_media_use_distinct_upstream_batches(media_module, media_store):
    paths = []
    for index in range(19):
        relative = f'album/image-{index}.png'
        (media_store.outputs / relative).write_bytes(f'cover {index}'.encode())
        paths.append(relative)
    media = capture(media_module, media_store, paths)
    assert [entry['path'] for entry in media] == paths
    assert len(media_store.for_turn('publication-session', 'prepare-turn')) == 19
    assert len({entry['artifactId'] for entry in media}) == 19


def test_same_capture_call_reuses_approved_snapshots_after_source_changes(media_module, media_store):
    first = capture(media_module, media_store, ['album/cover.png'])
    (media_store.outputs / 'album/cover.png').write_bytes(b'changed')
    assert capture(media_module, media_store, ['album/cover.png']) == first
    (media_store.outputs / 'album/cover.png').unlink()
    assert capture(media_module, media_store, ['album/cover.png']) == first


def test_native_ids_are_actor_ip_scoped_and_verified(media_module, media_store, tmp_path):
    first = capture(media_module, media_store, ['album/cover.png'])[0]
    assert capture(media_module, media_store, artifact_ids=[first['artifactId']]) == [first]
    other = ChatArtifactStore(tmp_path / 'different-actor-ip', media_store.outputs, media_store.workspace, 'other-ip')
    with pytest.raises(ArtifactError) as caught:
        capture(media_module, other, artifact_ids=[first['artifactId']])
    assert caught.value.status_code == 404


@pytest.mark.parametrize('relative', ['.private.png', 'album/auth.json', 'album/credentials.json',
    '_login/qr.png', '_sessions/hidden.mp4', '../external.png'])
def test_private_or_escaping_paths_are_rejected(media_module, media_store, tmp_path, relative):
    path = media_store.outputs / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'must-not-publish')
    with pytest.raises(ArtifactError):
        capture(media_module, media_store, [relative])


def test_symlink_source_and_non_media_are_rejected(media_module, media_store, tmp_path):
    private = tmp_path / 'private.png'
    private.write_bytes(b'private')
    (media_store.outputs / 'album/link.png').symlink_to(private)
    (media_store.outputs / 'album/report.pdf').write_bytes(b'not publisher media')
    for relative in ['album/link.png', 'album/report.pdf']:
        with pytest.raises(ArtifactError):
            capture(media_module, media_store, [relative], call=relative)


@pytest.mark.parametrize('field,value', [
    ('artifactId', 'f' * 32), ('path', 'album/other.png'), ('name', 'other.png'),
    ('mime', 'video/mp4'), ('revision', 8), ('sha256', '0' * 64), ('size', 1),
    ('bundleSha256', 'b' * 64), ('url', '/api/media/album/cover.png'),
    ('workspacePath', 'outputs/album/other.png'),
])
def test_staging_rejects_changed_subject_media_fields(media_module, media_store, tmp_path, field, value):
    media = capture(media_module, media_store, ['album/cover.png'])
    altered = {**media[0], field:value}
    with pytest.raises(ArtifactError):
        media_module.stage_publication_media(media_store, [altered], tmp_path / 'task')
    assert not (tmp_path / 'task').exists() or not list((tmp_path / 'task').iterdir())


@pytest.mark.parametrize('field', ['revision', 'size'])
def test_staging_does_not_treat_booleans_as_approved_numeric_fields(media_module, media_store, tmp_path, field):
    (media_store.outputs / 'album/cover.png').write_bytes(b'x')
    media = capture(media_module, media_store, ['album/cover.png'])
    altered = {**media[0], field:True}
    with pytest.raises(ArtifactError):
        media_module.stage_publication_media(media_store, [altered], tmp_path / 'task')


def test_legacy_and_corrupt_snapshots_never_fall_back_to_sources(media_module, media_store, tmp_path):
    original = capture(media_module, media_store, ['album/cover.png'])[0]
    legacy = {**media_store.get(original['artifactId']), 'artifactId':'a' * 32, 'storageMode':'legacy-current'}
    with media_store._connect() as connection:
        connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',
            (legacy['artifactId'], 'legacy', 'legacy', legacy['path'], 0, json.dumps(legacy)))
    with pytest.raises(ArtifactError):
        capture(media_module, media_store, artifact_ids=[legacy['artifactId']])
    blob = media_store.directory / 'artifact_blobs' / original['sha256']
    blob.chmod(0o600)
    blob.write_bytes(b'corrupt snapshot')
    (media_store.outputs / 'album/cover.png').write_bytes(b'current-source-must-not-leak')
    with pytest.raises(ArtifactError):
        media_module.stage_publication_media(media_store, [original], tmp_path / 'task')
    assert not (tmp_path / 'task').exists() or not list((tmp_path / 'task').iterdir())


def test_failure_cleans_earlier_staged_files_but_preserves_host_state(media_module, media_store, tmp_path, monkeypatch):
    media = capture(media_module, media_store, ['album/cover.png', 'album/clip.mp4'])
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'host-state.json').write_bytes(b'host owned')
    original = media_store.open_content
    def fail_second(identifier, **kwargs):
        if identifier == media[1]['artifactId']:
            raise ArtifactError('injected unavailable snapshot', 409)
        return original(identifier, **kwargs)
    monkeypatch.setattr(media_store, 'open_content', fail_second)
    with pytest.raises(ArtifactError):
        media_module.stage_publication_media(media_store, media, task)
    assert [path.name for path in task.iterdir()] == ['host-state.json']
    assert (task / 'host-state.json').read_bytes() == b'host owned'


def test_cancellation_during_copy_leaves_no_staged_media(media_module, media_store, tmp_path):
    (media_store.outputs / 'album/clip.mp4').write_bytes(b'v' * (3 * 1024 * 1024))
    media = capture(media_module, media_store, ['album/clip.mp4'])
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 4
    with pytest.raises(ArtifactError) as caught:
        media_module.stage_publication_media(media_store, media, tmp_path / 'task', cancelled=cancelled)
    assert caught.value.status_code == 499
    assert not (tmp_path / 'task').exists() or not list((tmp_path / 'task').iterdir())


def test_staging_never_chases_bytes_beyond_the_approved_size(media_module, media_store, tmp_path, monkeypatch):
    media = capture(media_module, media_store, ['album/cover.png'])
    original = media_store.open_content
    reads = []
    class BoundedRead:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def seek(self, *args):
            return self.stream.seek(*args)
        def read(self, amount):
            reads.append(amount)
            assert amount <= media[0]['size'] + 1, 'Unapproved growth was admitted to a staging copy'
            return self.stream.read(amount)
    def grow_after_verification(identifier, **kwargs):
        stream = original(identifier, **kwargs)
        blob = media_store.directory / 'artifact_blobs' / media[0]['sha256']
        with blob.open('ab') as outgoing:
            outgoing.write(b'x' * (2 * 1024 * 1024))
        return BoundedRead(stream)
    monkeypatch.setattr(media_store, 'open_content', grow_after_verification)
    with pytest.raises(ArtifactError):
        media_module.stage_publication_media(media_store, media, tmp_path / 'task')
    assert not reads
    assert not list((tmp_path / 'task').iterdir())


def test_staging_directory_links_are_rejected(media_module, media_store, tmp_path):
    media = capture(media_module, media_store, ['album/cover.png'])
    private = tmp_path / 'elsewhere'
    private.mkdir()
    (private / 'keep').write_bytes(b'unchanged')
    (tmp_path / 'task').symlink_to(private, target_is_directory=True)
    with pytest.raises(ArtifactError):
        media_module.stage_publication_media(media_store, media, tmp_path / 'task')
    assert [path.name for path in private.iterdir()] == ['keep']


def test_an_existing_task_lock_is_preserved_as_a_conflict(media_module, media_store, tmp_path):
    media = capture(media_module, media_store, ['album/cover.png'])
    task = tmp_path / 'task'
    task.mkdir()
    (task / '.publication-stage.lock').write_bytes(b'another host operation')
    with pytest.raises(ArtifactError) as caught:
        media_module.stage_publication_media(media_store, media, task)
    assert caught.value.status_code == 409
    assert (task / '.publication-stage.lock').read_bytes() == b'another host operation'


def test_root_replacement_cannot_redirect_a_staged_publication(media_module, media_store, tmp_path, monkeypatch):
    from easel.artifact_blobs import ArtifactBlobStore
    media = capture(media_module, media_store, ['album/cover.png'])
    task, retained = tmp_path / 'task', tmp_path / 'retained-task'
    original = ArtifactBlobStore._publish
    replaced = False
    def replace_root(store, directory, temporary, name):
        nonlocal replaced
        if store.root == task and not replaced:
            replaced = True
            task.rename(retained)
            task.mkdir()
            (task / 'keep').write_bytes(b'replacement owned')
        return original(store, directory, temporary, name)
    monkeypatch.setattr(ArtifactBlobStore, '_publish', replace_root)
    with pytest.raises(ArtifactError):
        media_module.stage_publication_media(media_store, media, task)
    assert replaced
    assert [path.name for path in task.iterdir()] == ['keep']
    assert not list(retained.iterdir())


def test_inputs_are_exclusive_and_empty_media_remains_valid(media_module, media_store, tmp_path):
    with pytest.raises(ArtifactError):
        capture(media_module, media_store, ['album/cover.png'], ['a' * 32])
    assert capture(media_module, media_store) == []
    assert media_module.stage_publication_media(media_store, [], tmp_path / 'task') == []


def test_successful_staging_has_an_idempotent_owned_cleanup(media_module, media_store, tmp_path):
    media = capture(media_module, media_store, ['album/cover.png', 'album/clip.mp4'])
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'host-state.json').write_bytes(b'host state')
    staged = media_module.stage_publication_media(media_store, media, task)
    assert isinstance(staged, list)
    assert callable(getattr(staged, 'cleanup', None)), 'Late unclaimed staging needs explicit cleanup ownership'
    assert staged.cleanup() is True
    assert staged.cleanup() is True
    assert [path.name for path in task.iterdir()] == ['host-state.json']


def test_successful_cleanup_never_removes_a_replaced_file_or_root(media_module, media_store, tmp_path):
    media = capture(media_module, media_store, ['album/cover.png'])
    task = tmp_path / 'task'
    staged = media_module.stage_publication_media(media_store, media, task)
    replacement = task / 'replacement.png'
    replacement.write_bytes(b'replacement belongs to host')
    os.replace(replacement, staged[0])
    assert callable(getattr(staged, 'cleanup', None))
    assert staged.cleanup() is False
    assert Path(staged[0]).read_bytes() == b'replacement belongs to host'
    second = media_module.stage_publication_media(media_store, media, tmp_path / 'second-task')
    (tmp_path / 'second-task').rename(tmp_path / 'retired-task')
    (tmp_path / 'second-task').mkdir()
    Path(second[0]).write_bytes(b'new directory belongs to host')
    assert second.cleanup() is False
    assert Path(second[0]).read_bytes() == b'new directory belongs to host'


def test_empty_staging_also_exposes_cleanup_without_creating_a_directory(media_module, media_store, tmp_path):
    staged = media_module.stage_publication_media(media_store, [], tmp_path / 'empty')
    assert staged == [] and callable(getattr(staged, 'cleanup', None))
    assert staged.cleanup() is True
    assert not (tmp_path / 'empty').exists()
