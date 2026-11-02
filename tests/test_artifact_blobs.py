"""D08 persistent snapshots must not depend on mutable output paths."""
import hashlib
import importlib
import io
import errno
import os
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture
def blobs():
    return importlib.import_module('easel.artifact_blobs')


def read_blob(store, receipt):
    with store.open_blob(receipt['sha256'], receipt['size']) as stream:
        assert stream.tell() == 0
        return stream.read()


def test_snapshot_uses_the_open_source_and_survives_later_source_replacement(tmp_path, blobs):
    store = blobs.ArtifactBlobStore(tmp_path / 'private' / 'blobs')
    source = tmp_path / 'mutable.txt'
    source.write_bytes(b'original bytes')
    with source.open('rb') as stream:
        receipt = store.capture(stream)
        assert not stream.closed
    source.write_bytes(b'replaced source')
    assert receipt == {'sha256': hashlib.sha256(b'original bytes').hexdigest(), 'size': 14}
    assert read_blob(store, receipt) == b'original bytes'
    reopened = blobs.ArtifactBlobStore(tmp_path / 'private' / 'blobs')
    assert read_blob(reopened, receipt) == b'original bytes'


def test_capture_rewinds_a_verified_source_instead_of_silently_snapshotting_its_tail(tmp_path, blobs):
    store = blobs.ArtifactBlobStore(tmp_path / 'blobs')
    source = tmp_path / 'source'
    source.write_bytes(b'complete file')
    with source.open('rb') as stream:
        stream.seek(5)
        receipt = store.capture(stream)
    assert read_blob(store, receipt) == b'complete file'


def test_primary_files_are_streamed_in_finite_chunks_without_a_small_file_cap(tmp_path, blobs):
    data = b'a' * (5 * 1024 * 1024 + 17)
    requests = []

    class Input(io.BytesIO):
        def read(self, size=-1):
            requests.append(size)
            assert 0 < size <= 1024 * 1024
            return super().read(size)

    store = blobs.ArtifactBlobStore(tmp_path / 'blobs')
    receipt = store.capture(Input(data))
    assert receipt['size'] == len(data)
    assert receipt['sha256'] == hashlib.sha256(data).hexdigest()
    assert len(requests) > 5
    assert read_blob(store, receipt) == data


def test_same_content_concurrently_publishes_one_complete_blob(tmp_path, blobs):
    root = tmp_path / 'blobs'
    data = b'same verified content' * 1000

    def capture(_):
        return blobs.ArtifactBlobStore(root).capture_bytes(data)

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(capture, range(24)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert [path.name for path in root.iterdir()] == [hashlib.sha256(data).hexdigest()]
    assert read_blob(blobs.ArtifactBlobStore(root), receipts[0]) == data


def test_distinct_bytes_have_distinct_snapshots_and_zero_bytes_are_valid(tmp_path, blobs):
    store = blobs.ArtifactBlobStore(tmp_path / 'blobs')
    first = store.capture_bytes(b'one')
    second = store.capture_bytes(b'two')
    empty = store.capture_bytes(b'')
    assert len({first['sha256'], second['sha256'], empty['sha256']}) == 3
    assert read_blob(store, empty) == b''


def test_inclusive_byte_limit_rejects_growth_without_publishing_partial_content(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    exact = store.capture_bytes(b'abcd', max_bytes=4)
    assert read_blob(store, exact) == b'abcd'
    with pytest.raises(blobs.SnapshotError) as caught:
        store.capture_bytes(b'abcde', max_bytes=4)
    assert caught.value.status_code == 413
    assert sorted(path.name for path in root.iterdir()) == [exact['sha256']]


@pytest.mark.parametrize('limit', [-1, True, '4'])
def test_invalid_capture_bounds_fail_before_creating_files(tmp_path, blobs, limit):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.capture_bytes(b'x', max_bytes=limit)
    assert caught.value.status_code == 400
    assert list(root.iterdir()) == []


def test_cancelled_capture_cleans_its_temp_without_removing_an_existing_blob(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    accepted = store.capture_bytes(b'keep this')
    reads = []

    class Input(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    with pytest.raises(blobs.SnapshotCancelled) as caught:
        store.capture(Input(b'x' * (3 * 1024 * 1024)), cancelled=lambda: bool(reads))
    assert caught.value.status_code == 499
    assert read_blob(store, accepted) == b'keep this'
    assert sorted(path.name for path in root.iterdir()) == [accepted['sha256']]


def test_cancellation_before_capture_does_not_publish_anything(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    with pytest.raises(blobs.SnapshotCancelled):
        store.capture_bytes(b'x', cancelled=lambda: True)
    assert list(root.iterdir()) == []


def test_source_modified_during_capture_is_rejected(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    source = tmp_path / 'source'
    source.write_bytes(b'a' * (2 * 1024 * 1024))
    with source.open('rb') as incoming:
        class Input:
            changed = False
            def seek(self, *args):
                return incoming.seek(*args)
            def fileno(self):
                return incoming.fileno()
            def read(self, size):
                result = incoming.read(size)
                if not self.changed:
                    self.changed = True
                    with source.open('ab') as writer:
                        writer.write(b'changed')
                return result

        with pytest.raises(blobs.SnapshotError) as caught:
            store.capture(Input())
    assert caught.value.status_code == 409
    assert list(root.iterdir()) == []


def test_continuously_growing_verified_fd_is_bounded_by_its_initial_size(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    source = tmp_path / 'source'
    source.write_bytes(b'initial')
    reads = []
    with source.open('rb', buffering=0) as incoming:
        class GrowingInput:
            def seek(self, *args):
                return incoming.seek(*args)
            def fileno(self):
                return incoming.fileno()
            def read(self, amount):
                reads.append(amount)
                assert len(reads) <= 3, 'Capture must not chase an ever-growing file until EOF'
                with source.open('ab', buffering=0) as outgoing:
                    outgoing.write(b'x' * (1024 * 1024))
                return incoming.read(amount)
        with pytest.raises(blobs.SnapshotError) as caught:
            store.capture(GrowingInput())
    assert caught.value.status_code == 409
    assert max(reads) <= len(b'initial') + 1
    assert list(root.iterdir()) == []


def test_growing_bytes_io_is_bounded_by_its_initial_length(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    reads = []
    class GrowingInput(io.BytesIO):
        def read(self, amount):
            reads.append(amount)
            assert len(reads) <= 3, 'Memory-source length must bound capture too'
            position = self.tell()
            self.seek(0, os.SEEK_END)
            self.write(b'x' * (1024 * 1024))
            self.seek(position)
            return super().read(amount)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.capture(GrowingInput(b'initial'))
    assert caught.value.status_code == 409
    assert max(reads) <= len(b'initial') + 1
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('parent_link', [False, True])
def test_private_blob_root_refuses_symbolic_links(tmp_path, blobs, parent_link):
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(outside, target_is_directory=True)
    root = link / 'blobs' if parent_link else link
    with pytest.raises(blobs.SnapshotError) as caught:
        blobs.ArtifactBlobStore(root)
    assert caught.value.status_code == 403
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('replacement', ['symlink', 'directory'])
def test_replaced_blob_root_is_never_trusted_by_an_existing_store(tmp_path, blobs, replacement):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    root.rename(tmp_path / 'old-blobs')
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    if replacement == 'symlink':
        root.symlink_to(foreign, target_is_directory=True)
    else:
        root.mkdir()
    with pytest.raises(blobs.SnapshotError):
        store.capture_bytes(b'must not publish')
    assert list(foreign.iterdir()) == []
    assert list((tmp_path / 'old-blobs').iterdir()) == []


def test_root_swapped_while_reading_cleans_only_the_original_private_temp(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    moved = tmp_path / 'old-blobs'

    class Input(io.BytesIO):
        swapped = False
        def read(self, size=-1):
            if not self.swapped:
                self.swapped = True
                root.rename(moved)
                root.symlink_to(foreign, target_is_directory=True)
            return super().read(size)

    with pytest.raises(blobs.SnapshotError):
        store.capture(Input(b'private source bytes'))
    assert list(foreign.iterdir()) == []
    assert list(moved.iterdir()) == []


def test_corrupted_existing_blob_is_not_overwritten_or_served(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    receipt = store.capture_bytes(b'original')
    target = root / receipt['sha256']
    target.write_bytes(b'corrupt!')
    with pytest.raises(blobs.SnapshotError) as opened:
        store.open_blob(receipt['sha256'], receipt['size'])
    assert opened.value.status_code == 409
    with pytest.raises(blobs.SnapshotError):
        store.capture_bytes(b'original')
    assert target.read_bytes() == b'corrupt!'
    assert sorted(path.name for path in root.iterdir()) == [receipt['sha256']]


def test_blob_link_and_non_regular_entry_cannot_be_opened(tmp_path, blobs):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    outside = tmp_path / 'private'
    outside.write_bytes(b'private')
    digest = hashlib.sha256(b'private').hexdigest()
    (root / digest).symlink_to(outside)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.open_blob(digest, 7)
    assert caught.value.status_code == 403
    (root / digest).unlink()
    (root / digest).mkdir()
    with pytest.raises(blobs.SnapshotError):
        store.open_blob(digest, 7)


@pytest.mark.parametrize('digest,size', [('bad', 1), ('a' * 64, -1), ('a' * 64, True), ('A' * 64, 1)])
def test_blob_lookup_requires_canonical_hash_and_nonnegative_integer_size(tmp_path, blobs, digest, size):
    store = blobs.ArtifactBlobStore(tmp_path / 'blobs')
    with pytest.raises(blobs.SnapshotError) as caught:
        store.open_blob(digest, size)
    assert caught.value.status_code == 400


def test_missing_blob_is_explicitly_unavailable(tmp_path, blobs):
    store = blobs.ArtifactBlobStore(tmp_path / 'blobs')
    with pytest.raises(blobs.SnapshotError) as caught:
        store.open_blob('a' * 64, 1)
    assert caught.value.status_code == 404


def test_publish_failure_cleans_temporary_bytes(tmp_path, blobs, monkeypatch):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    def fail(*args, **kwargs):
        raise OSError('synthetic-publish-failure')
    monkeypatch.setattr(os, 'link', fail)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.capture_bytes(b'complete but unpublished')
    assert caught.value.status_code == 500
    assert 'synthetic-publish-failure' not in str(caught.value)
    assert list(root.iterdir()) == []


def test_atomic_publication_exposes_no_blob_name_until_all_bytes_are_written(tmp_path, blobs, monkeypatch):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    data = b'complete publication' * 10000
    digest = hashlib.sha256(data).hexdigest()
    original_link = os.link
    observed = []
    def publish(source, destination, *args, **kwargs):
        assert destination == digest
        with pytest.raises(blobs.SnapshotError) as missing:
            store.open_blob(digest, len(data))
        assert missing.value.status_code == 404
        observed.append(True)
        return original_link(source, destination, *args, **kwargs)
    monkeypatch.setattr(os, 'link', publish)
    receipt = store.capture_bytes(data)
    assert observed == [True]
    assert read_blob(store, receipt) == data


def test_blob_growth_during_integrity_check_reads_at_most_declared_size_plus_one(tmp_path, blobs, monkeypatch):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    receipt = store.capture_bytes(b'original')
    target = root / receipt['sha256']
    original_fdopen = os.fdopen
    reads, wrapped = [], []

    class Reader:
        changed = False
        def __init__(self, stream):
            self.stream = stream
            wrapped.append(self)
        def read(self, amount):
            reads.append(amount)
            if not self.changed:
                self.changed = True
                with target.open('ab') as outgoing:
                    outgoing.write(b'x' * (2 * 1024 * 1024))
            return self.stream.read(amount)
        def __getattr__(self, name):
            return getattr(self.stream, name)

    def fdopen(descriptor, mode, *args, **kwargs):
        stream = original_fdopen(descriptor, mode, *args, **kwargs)
        return Reader(stream) if mode == 'rb' else stream

    monkeypatch.setattr(os, 'fdopen', fdopen)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.open_blob(receipt['sha256'], receipt['size'])
    assert caught.value.status_code == 409
    assert max(reads) <= receipt['size'] + 1
    assert all(reader.closed for reader in wrapped)


def test_fsync_failure_does_not_publish_or_leave_private_temp(tmp_path, blobs, monkeypatch):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    def fail(descriptor):
        raise OSError('synthetic-fsync-failure')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.capture_bytes(b'not durable')
    assert caught.value.status_code == 500
    assert list(root.iterdir()) == []


def test_deduplicated_capture_does_not_ignore_a_directory_durability_failure(tmp_path, blobs, monkeypatch):
    root = tmp_path / 'blobs'
    store = blobs.ArtifactBlobStore(root)
    original = store.capture_bytes(b'already present')
    original_fsync = os.fsync
    def fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError('synthetic-directory-fsync-failure')
        return original_fsync(descriptor)
    monkeypatch.setattr(os, 'fsync', fsync)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.capture_bytes(b'already present')
    assert caught.value.status_code == 500
    assert read_blob(store, original) == b'already present'
    assert sorted(path.name for path in root.iterdir()) == [original['sha256']]


def test_portable_guard_captures_and_opens_without_directory_fd_support(tmp_path, blobs, monkeypatch):
    monkeypatch.setattr(blobs, '_SECURE_DIR_FD', False)
    store = blobs.ArtifactBlobStore(tmp_path / 'portable')
    receipt = store.capture_bytes(b'portable snapshot')
    assert read_blob(store, receipt) == b'portable snapshot'


def test_portable_cancel_cleans_temp_and_preserves_existing_snapshots(tmp_path, blobs, monkeypatch):
    monkeypatch.setattr(blobs, '_SECURE_DIR_FD', False)
    root = tmp_path / 'portable'
    store = blobs.ArtifactBlobStore(root)
    accepted = store.capture_bytes(b'keep')
    reads = []
    class Input(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)
    with pytest.raises(blobs.SnapshotCancelled):
        store.capture(Input(b'x' * (2 * 1024 * 1024)), cancelled=lambda: bool(reads))
    assert read_blob(store, accepted) == b'keep'
    assert [path.name for path in root.iterdir()] == [accepted['sha256']]


def test_portable_lookup_detects_content_tampering_and_does_not_fallback(tmp_path, blobs, monkeypatch):
    monkeypatch.setattr(blobs, '_SECURE_DIR_FD', False)
    root = tmp_path / 'portable'
    store = blobs.ArtifactBlobStore(root)
    receipt = store.capture_bytes(b'accepted')
    (root / receipt['sha256']).write_bytes(b'tampered')
    with pytest.raises(blobs.SnapshotError) as caught:
        store.open_blob(receipt['sha256'], receipt['size'])
    assert caught.value.status_code == 409


def test_portable_guard_rejects_root_parent_and_blob_symbolic_links(tmp_path, blobs, monkeypatch):
    monkeypatch.setattr(blobs, '_SECURE_DIR_FD', False)
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(blobs.SnapshotError) as caught:
        blobs.ArtifactBlobStore(link / 'blobs')
    assert caught.value.status_code == 403
    store = blobs.ArtifactBlobStore(tmp_path / 'portable')
    secret = outside / 'secret'
    secret.write_bytes(b'not a snapshot')
    digest = hashlib.sha256(b'not a snapshot').hexdigest()
    (store.root / digest).symlink_to(secret)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.open_blob(digest, 14)
    assert caught.value.status_code == 403


def test_portable_existing_store_rejects_a_replaced_root(tmp_path, blobs, monkeypatch):
    monkeypatch.setattr(blobs, '_SECURE_DIR_FD', False)
    root = tmp_path / 'portable'
    store = blobs.ArtifactBlobStore(root)
    root.rename(tmp_path / 'original')
    root.mkdir()
    with pytest.raises(blobs.SnapshotError) as caught:
        store.capture_bytes(b'not accepted')
    assert caught.value.status_code == 409
    assert list(root.iterdir()) == []


def test_portable_concurrent_publish_keeps_one_blob_and_cleans_each_temp(tmp_path, blobs, monkeypatch):
    monkeypatch.setattr(blobs, '_SECURE_DIR_FD', False)
    root = tmp_path / 'portable'
    data = b'portable concurrency' * 1000
    def capture(_):
        return blobs.ArtifactBlobStore(root).capture_bytes(data)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(capture, range(16)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert [path.name for path in root.iterdir()] == [receipts[0]['sha256']]
    assert read_blob(blobs.ArtifactBlobStore(root), receipts[0]) == data


def test_capture_never_reopens_a_verified_source_by_its_name(tmp_path, blobs):
    store = blobs.ArtifactBlobStore(tmp_path / 'blobs')
    source = tmp_path / 'source'
    source.write_bytes(b'fd-owned bytes')
    with source.open('rb') as incoming:
        class Input:
            @property
            def name(self):
                raise AssertionError('The source pathname must not be reopened')
            def seek(self, *args):
                return incoming.seek(*args)
            def fileno(self):
                return incoming.fileno()
            def read(self, amount):
                return incoming.read(amount)
        receipt = store.capture(Input())
        assert not incoming.closed
    assert read_blob(store, receipt) == b'fd-owned bytes'


@pytest.mark.parametrize('error', [NotImplementedError('synthetic missing hardlink primitive'),
                                 OSError(errno.EOPNOTSUPP, 'synthetic filesystem without links')])
def test_filesystem_without_atomic_hardlinks_reports_specific_failure_and_cleans_temp(tmp_path, blobs, monkeypatch, error):
    monkeypatch.setattr(blobs, '_SECURE_DIR_FD', False)
    root = tmp_path / 'portable'
    store = blobs.ArtifactBlobStore(root)
    def unsupported(*args, **kwargs):
        raise error
    monkeypatch.setattr(os, 'link', unsupported)
    with pytest.raises(blobs.SnapshotError) as caught:
        store.capture_bytes(b'must not pretend to be durable')
    assert caught.value.status_code == 501
    assert '原子' in str(caught.value)
    assert list(root.iterdir()) == []
