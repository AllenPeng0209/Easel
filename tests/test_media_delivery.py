"""Real media fixtures protect original bytes, progressive playback, and private validators."""
from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image

pytestmark = pytest.mark.access_integration


@pytest.fixture
def video(tmp_path):
    path = tmp_path / 'source' / 'clip.mp4'
    path.parent.mkdir()
    subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi',
                    '-i', 'color=c=blue:s=128x160:r=12', '-t', '0.5', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(path)], check=True, capture_output=True, timeout=15)
    return path


def atoms(path):
    result = {}
    with path.open('rb') as stream:
        offset = 0
        while offset + 8 <= path.stat().st_size:
            stream.seek(offset)
            size, name = struct.unpack('>I4s', stream.read(8))
            if size == 1:
                size = struct.unpack('>Q', stream.read(8))[0]
            if size < 8:
                break
            result[name.decode()] = offset
            offset += size
    return result


def test_playback_copy_moves_metadata_to_front_and_keeps_original_unchanged(tmp_path, video):
    from easel.media_delivery import MediaDelivery
    original = video.read_bytes()
    assert atoms(video)['moov'] > atoms(video)['mdat']
    delivery = MediaDelivery(tmp_path / 'private-cache', video.parent)
    ready = delivery.playback(video, quality='original')
    assert ready != video
    assert atoms(ready)['moov'] < atoms(ready)['mdat']
    assert video.read_bytes() == original
    before = ready.stat().st_mtime_ns
    assert delivery.playback(video, quality='original').stat().st_mtime_ns == before
    meta = __import__('json').loads(ready.with_suffix('.json').read_text())
    assert meta['source_sha256'] == hashlib.sha256(original).hexdigest()


def test_private_delivery_preserves_range_lengths_and_revalidates_per_identity(tmp_path, video):
    from easel.media_delivery import MediaDelivery
    delivery = MediaDelivery(tmp_path / 'cache', video.parent)
    app = FastAPI()

    @app.get('/clip')
    def get_clip(request: Request):
        return delivery.response(request, 'clip.mp4', actor_scope=request.headers.get('x-test-user', 'first'))

    client = TestClient(app)
    first = client.get('/clip', headers={'Range': 'bytes=0-31'})
    assert first.status_code == 206
    assert first.content == video.read_bytes()[:32]
    assert first.headers['content-range'] == f'bytes 0-31/{video.stat().st_size}'
    assert first.headers['content-length'] == '32'
    assert first.headers['cache-control'] == 'private, no-cache'
    assert client.get('/clip', headers={'If-None-Match': first.headers['etag']}).status_code == 304
    switched = client.get('/clip', headers={'If-None-Match': first.headers['etag'], 'x-test-user': 'second'})
    assert switched.status_code == 200
    assert switched.headers['etag'] != first.headers['etag']
    assert client.get('/clip', headers={'Range': 'bytes=999999-'}).status_code == 416


def test_media_paths_and_symlink_sources_cannot_escape_the_ip_root(tmp_path):
    from easel.media_delivery import MediaDelivery
    root = tmp_path / 'source'
    root.mkdir()
    outside = tmp_path / 'private.txt'
    outside.write_text('another workspace')
    (root / 'linked.txt').symlink_to(outside)
    delivery = MediaDelivery(tmp_path / 'cache', root)
    for path in ['../private.txt', 'linked.txt', '/private.txt', 'dir\\private.txt']:
        with pytest.raises(ValueError):
            delivery.source(path)


def test_thumbnail_has_bounded_dimensions_and_preserves_original_image(tmp_path):
    from easel.media_delivery import MediaDelivery
    root = tmp_path / 'source'
    root.mkdir()
    source = root / 'poster.jpg'
    Image.new('RGB', (1600, 1200), '#18747a').save(source)
    original = source.read_bytes()
    delivery = MediaDelivery(tmp_path / 'cache', root)
    thumb = delivery.thumbnail(source, width=192)
    with Image.open(thumb) as result:
        assert result.size == (192, 144)
        assert result.format == 'WEBP'
    assert source.read_bytes() == original
    assert thumb.stat().st_size < source.stat().st_size


def test_smooth_preview_keeps_audio_duration_and_original_source(tmp_path):
    from easel.media_delivery import MediaDelivery
    import json
    root = tmp_path / 'source'
    root.mkdir()
    source = root / 'with-audio.mp4'
    subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=640x800:rate=12', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                    '-t', '0.5', '-c:v', 'libx264', '-preset', 'veryfast', '-threads', '1', '-c:a', 'aac', str(source)],
                   check=True, capture_output=True, timeout=15)
    original = source.read_bytes()
    preview = MediaDelivery(tmp_path / 'cache', root).playback(source, quality='low')
    assert preview != source
    probe = json.loads(subprocess.check_output([shutil.which('ffprobe'), '-v', 'error', '-show_entries',
        'stream=codec_type,width,height:format=duration', '-of', 'json', str(preview)]))
    assert any(stream['codec_type'] == 'audio' for stream in probe['streams'])
    assert next(stream['width'] for stream in probe['streams'] if stream['codec_type'] == 'video') == 360
    assert abs(float(probe['format']['duration']) - 0.5) < 0.1
    assert atoms(preview)['moov'] < atoms(preview)['mdat']
    assert source.read_bytes() == original
