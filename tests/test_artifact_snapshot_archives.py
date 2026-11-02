"""ZIP previews must read immutable snapshot bytes, never adjacent live files."""
from __future__ import annotations

import importlib
import io
import stat
import struct
import warnings
import zipfile

import pytest


def reader_class():
    try:
        return importlib.import_module('easel.artifact_archives').ArchiveSnapshotReader
    except ModuleNotFoundError:
        pytest.fail('ArchiveSnapshotReader is not implemented')


def archive_bytes(entries, *, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(stream, 'w', compression=compression) as archive:
            for name, data in entries:
                archive.writestr(name, data)
    return stream.getvalue()


def corrupt_member(blob, name):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        info = archive.getinfo(name)
        offset = info.header_offset
    name_length, extra_length = struct.unpack_from('<HH', blob, offset + 26)
    result = bytearray(blob)
    result[offset + 30 + name_length + extra_length] ^= 1
    return bytes(result)


def test_snapshot_zip_preserves_media_kinds_and_never_reads_live_neighbors(tmp_path):
    live = tmp_path / 'delivery.zip'
    original = archive_bytes([('图片.png', b'original-image'), ('film.mp4', b'original-video'),
                              ('voice.mp3', b'audio'), ('report.pdf', b'pdf'), ('web/index.html', b'<h1>old</h1>')])
    live.write_bytes(original)
    snapshot = io.BytesIO(original)
    live.write_bytes(archive_bytes([('图片.png', b'CHANGED')]))
    with reader_class()(snapshot) as reader:
        items = reader.index()
        assert [item['kind'] for item in items] == ['image', 'video', 'audio', 'pdf', 'html']
        assert [item['path'] for item in items] == ['图片.png', 'film.mp4', 'voice.mp3', 'report.pdf', 'web/index.html']
        assert items[0]['name'] == '图片.png'
        with reader.open_member(items[0]['index']) as member:
            assert member.stream.read() == b'original-image'
            assert member.size == 14
            assert member.mime == 'image/png'
            assert member.stream.seekable()
    assert snapshot.closed
    assert sorted(path.name for path in tmp_path.iterdir()) == ['delivery.zip']


def test_private_unsafe_and_symlink_members_never_enter_preview_index():
    link = zipfile.ZipInfo('link.png')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    entries = [(name, b'secret') for name in ['../outside.png', '/abs.png', r'a\evil.png', 'a//bad.png',
        '.hidden.png', '_sessions/a.png', 'web/.env.png', 'cookies.png', 'secret.png', 'https:evil.png']]
    entries += [(link, b'/etc/passwd'), ('good.PNG', b'ok'), ('notes.txt', b'not previewable')]
    with reader_class()(io.BytesIO(archive_bytes(entries))) as reader:
        assert [item['path'] for item in reader.index()] == ['good.PNG', 'notes.txt']
        assert [item['kind'] for item in reader.index()] == ['image', 'file']


def test_duplicate_names_keep_explicit_indices_and_each_original_version():
    with reader_class()(io.BytesIO(archive_bytes([('index.html', b'one'), ('index.html', b'two')]))) as reader:
        assert [item['index'] for item in reader.index()] == [0, 1]
        with reader.open_member(0) as first, reader.open_member(1) as second:
            assert first.stream.read() == b'one'
            assert second.stream.read() == b'two'


def test_related_duplicate_path_reports_ambiguity_instead_of_guessing_last_member():
    blob = archive_bytes([('index.html', b'html'), ('app.css', b'one'), ('app.css', b'two')])
    with reader_class()(io.BytesIO(blob)) as reader:
        with pytest.raises(ValueError, match='重复|歧义') as error:
            reader.read_related(0, 'app.css')
        assert error.value.status_code == 409


def test_related_duplicate_does_not_guess_when_one_occurrence_is_not_readable():
    blob = bytearray(archive_bytes([('index.html', b'html'), ('app.css', b'one'), ('app.css', b'two')]))
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        third = archive.infolist()[2]
    # Only the second CSS occurrence is encrypted; choosing the first would
    # still silently invent which same-path version the HTML author intended.
    central = blob.index(b'PK\x01\x02')
    central = blob.index(b'PK\x01\x02', central + 1)
    central = blob.index(b'PK\x01\x02', central + 1)
    struct.pack_into('<H', blob, third.header_offset + 6, 1)
    struct.pack_into('<H', blob, central + 8, 1)
    with reader_class()(io.BytesIO(blob)) as reader:
        with pytest.raises(ValueError, match='重复|歧义') as error:
            reader.read_related(0, 'app.css')
        assert error.value.status_code == 409


def test_generic_downloads_do_not_consume_the_original_media_preview_budget():
    entries = [(f'notes-{index}.docx', b'document') for index in range(120)]
    entries += [(f'image-{index}.png', b'image') for index in range(105)]
    with reader_class()(io.BytesIO(archive_bytes(entries))) as reader:
        items = reader.index()
        assert sum(item['kind'] == 'file' for item in items) == 120
        assert sum(item['kind'] == 'image' for item in items) == 100
        assert len(items) == 220
        with reader.open_member(0) as member:
            assert member.stream.read() == b'document'
            assert member.name == 'notes-0.docx'


def test_index_is_bounded_but_does_not_truncate_normal_media_bytes():
    video = b'video-data-' * (1024 * 1024)
    data = archive_bytes([('film.mp4', video)] + [(f'card-{number}.png', b'image') for number in range(105)])
    with reader_class()(io.BytesIO(data)) as reader:
        items = reader.index()
        assert len(items) == 100
        with reader.open_member(items[0]['index']) as member:
            assert member.size == len(video) > 4 * 1024 * 1024
            member.stream.seek(len(video) - 13)
            assert member.stream.read() == video[-13:]


def test_archive_member_count_limit_is_explicit():
    data = archive_bytes([(f'f-{number}.txt', b'') for number in range(1001)])
    with pytest.raises(ValueError, match='文件过多|数量'):
        reader_class()(io.BytesIO(data))


def test_html_resources_resolve_inside_zip_and_preserve_already_decoded_names():
    data = archive_bytes([('web/index.html', b'<h1>old</h1>'), ('web/app.css', b'body{color:red}'),
                          ('web/app.js', b'window.value=1'), ('shared/logo.png', b'image'),
                          ('web/name%2Fpart.png', b'literal-percent'), ('web/theme#dark.css', b'dark')])
    with reader_class()(io.BytesIO(data)) as reader:
        index = reader.index()[0]['index']
        assert reader.read_related(index, 'app.css') == (b'body{color:red}', 'text/css')
        assert reader.read_related(index, 'app.js')[0] == b'window.value=1'
        assert reader.read_related(index, '../shared/logo.png')[0] == b'image'
        assert reader.read_related(index, 'name%2Fpart.png')[0] == b'literal-percent'
        assert reader.read_related(index, 'theme#dark.css')[0] == b'dark'


@pytest.mark.parametrize('path', ['../../outside.png', '/etc/file.png', '//host/a.png', 'https://host/a.png',
    'data:image/png,foo', r'..\a.png', '.hidden.png', '_private/a.png', 'cookies.js', 'config.json', 'bad\x00.png'])
def test_html_resource_path_refuses_private_external_and_unsupported_targets(path):
    data = archive_bytes([('web/index.html', b'html'), ('web/.hidden.png', b'private'),
                          ('web/cookies.js', b'private'), ('web/config.json', b'private')])
    with reader_class()(io.BytesIO(data)) as reader:
        with pytest.raises(ValueError):
            reader.read_related(reader.index()[0]['index'], path)


def test_related_resource_limit_does_not_apply_to_regular_video_members():
    data = archive_bytes([('index.html', b'html'), ('big.css', b'x' * (4 * 1024 * 1024 + 1))])
    with reader_class()(io.BytesIO(data)) as reader:
        with pytest.raises(ValueError, match='4 MiB'):
            reader.read_related(reader.index()[0]['index'], 'big.css')


def test_corrupt_preview_crc_fails_before_a_member_can_be_accepted():
    blob = corrupt_member(archive_bytes([('image.png', b'original')]), 'image.png')
    with reader_class()(io.BytesIO(blob)) as reader:
        assert reader.index()[0]['crcVerified'] is False
        with pytest.raises(ValueError, match='CRC|损坏'):
            reader.open_member(0)


def test_corrupt_related_crc_is_not_returned_as_successful_html_asset():
    blob = corrupt_member(archive_bytes([('index.html', b'html'), ('app.css', b'original')]), 'app.css')
    with reader_class()(io.BytesIO(blob)) as reader:
        with pytest.raises(ValueError, match='CRC|损坏'):
            reader.read_related(reader.index()[0]['index'], 'app.css')


def test_bad_zip_closes_owned_snapshot_and_has_an_explicit_error():
    stream = io.BytesIO(b'not-a-zip')
    with pytest.raises(ValueError, match='ZIP|压缩包'):
        reader_class()(stream)
    assert stream.closed


def test_reader_close_closes_members_and_root_handle():
    stream = io.BytesIO(archive_bytes([('one.png', b'image')]))
    reader = reader_class()(stream)
    member = reader.open_member(0)
    reader.close()
    assert member.stream.closed
    assert stream.closed
    reader.close()


def test_zip_bomb_ratio_guard_is_explicit_without_extracting_any_files():
    blob = archive_bytes([('huge.svg', b'x' * (16 * 1024 * 1024))], compression=zipfile.ZIP_BZIP2)
    with pytest.raises(ValueError, match='压缩比|解压'):
        reader_class()(io.BytesIO(blob))


def test_index_reads_metadata_without_decompressing_large_media():
    class ObservedBytes(io.BytesIO):
        read_bytes = 0
        def read(self, size=-1):
            data = super().read(size)
            self.read_bytes += len(data)
            return data
    stream = ObservedBytes(archive_bytes([('film.mp4', b'v' * (12 * 1024 * 1024))]))
    with reader_class()(stream) as reader:
        item = reader.index()[0]
        assert item['size'] == 12 * 1024 * 1024
        assert item['crcVerified'] is False
        assert stream.read_bytes < 100_000


def test_real_open_descriptor_remains_bound_when_source_path_is_replaced(tmp_path):
    source = tmp_path / 'snapshot.zip'
    source.write_bytes(archive_bytes([('one.png', b'original')]))
    descriptor = source.open('rb')
    replacement = tmp_path / 'new.zip'
    replacement.write_bytes(archive_bytes([('one.png', b'changed')]))
    replacement.replace(source)
    with reader_class()(descriptor) as reader:
        with reader.open_member(0) as member:
            assert member.stream.read() == b'original'
    assert descriptor.closed


@pytest.mark.parametrize('index', [-1, 0, '2', True, 999])
def test_guessing_private_or_nonpreview_indices_never_opens_their_bytes(index):
    blob = archive_bytes([('.hidden.png', b'private'), ('app.css', b'css'), ('good.png', b'ok')])
    with reader_class()(io.BytesIO(blob)) as reader:
        assert [item['index'] for item in reader.index()] == [1, 2]
        with pytest.raises(ValueError):
            reader.open_member(index)


@pytest.mark.parametrize('encrypted,method', [(True, None), (False, 99)])
def test_encrypted_and_unsupported_members_are_not_advertised_as_previewable(encrypted, method):
    blob = bytearray(archive_bytes([('one.png', b'content')]))
    central = blob.index(b'PK\x01\x02')
    if encrypted:
        struct.pack_into('<H', blob, 6, 1)
        struct.pack_into('<H', blob, central + 8, 1)
    if method is not None:
        struct.pack_into('<H', blob, 8, method)
        struct.pack_into('<H', blob, central + 10, method)
    with reader_class()(io.BytesIO(blob)) as reader:
        assert reader.index() == []
        with pytest.raises(ValueError):
            reader.open_member(0)
