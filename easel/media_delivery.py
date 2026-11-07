"""Private, versioned playback derivatives without changing accepted source media."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import tempfile
import threading

from starlette.requests import Request
from starlette.responses import FileResponse, Response

_MEDIA_WORK = threading.BoundedSemaphore(1)


def _progressive(path: Path) -> bool:
    """Inspect top-level MP4 boxes; never read the video body to find its index."""
    with path.open('rb') as stream:
        total, offset = path.stat().st_size, 0
        while offset + 8 <= total:
            stream.seek(offset)
            size, kind = struct.unpack('>I4s', stream.read(8))
            if size == 1:
                extra = stream.read(8)
                if len(extra) != 8:
                    return False
                size = struct.unpack('>Q', extra)[0]
            if kind == b'moov':
                return True
            if kind == b'mdat' or size < 8:
                return False
            offset += size
    return False


class MediaDelivery:
    def __init__(self, cache_root: Path, source_root: Path):
        if Path(cache_root).is_symlink():
            raise ValueError('Media cache cannot be a symlink')
        self.cache = Path(cache_root).resolve()
        self.root = Path(source_root).resolve()

    def source(self, relative: str) -> Path:
        path = PurePosixPath(relative)
        if (not relative or path.is_absolute() or '..' in path.parts or '\\' in relative
                or any(ord(char) < 32 for char in relative)):
            raise ValueError('Invalid media path')
        candidate = self.root
        for part in path.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise ValueError('Media symlinks are not supported')
        if not candidate.is_file() or not candidate.resolve().is_relative_to(self.root):
            raise ValueError('Media file is unavailable')
        return candidate

    def _version(self, source: Path, variant: str) -> str:
        safe = self.source(source.relative_to(self.root).as_posix())
        info = safe.stat()
        value = f'v1\n{safe}\n{info.st_ino}\n{info.st_size}\n{info.st_mtime_ns}\n{variant}'
        return hashlib.sha256(value.encode()).hexdigest()

    def _variant(self, source: Path, variant: str, suffix: str, produce) -> Path:
        key = self._version(source, variant)
        destination = self.cache / (key + suffix)
        if destination.is_file() and not destination.is_symlink():
            return destination
        with _MEDIA_WORK:
            if destination.is_file() and not destination.is_symlink():
                return destination
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.cache.is_symlink():
                raise ValueError('Invalid private media cache')
            descriptor, name = tempfile.mkstemp(dir=self.cache, prefix='.media-', suffix=suffix)
            os.close(descriptor)
            temporary = Path(name)
            metadata_temporary = None
            try:
                produce(temporary)
                if not temporary.stat().st_size or self._version(source, variant) != key:
                    return source
                digest = hashlib.sha256()
                with source.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                if self._version(source, variant) != key:
                    return source
                os.chmod(temporary, 0o600)
                metadata = {'source_sha256': digest.hexdigest(), 'source_bytes': source.stat().st_size,
                            'variant': variant, 'bytes': temporary.stat().st_size}
                handle, meta_name = tempfile.mkstemp(dir=self.cache, prefix='.media-', suffix='.json')
                metadata_temporary = Path(meta_name)
                with os.fdopen(handle, 'w') as stream:
                    json.dump(metadata, stream)
                os.replace(metadata_temporary, destination.with_suffix('.json'))
                os.replace(temporary, destination)
                return destination
            except (OSError, ValueError, subprocess.SubprocessError):
                return source
            finally:
                temporary.unlink(missing_ok=True)
                if metadata_temporary is not None:
                    metadata_temporary.unlink(missing_ok=True)

    def playback(self, source: Path, *, quality: str = 'original') -> Path:
        if quality not in {'original', 'low'}:
            raise ValueError('Unsupported playback quality')
        self._version(source, 'validate')
        if source.suffix.lower() != '.mp4':
            return source
        try:
            if quality == 'original' and _progressive(source):
                return source
        except (OSError, ValueError, struct.error):
            return source
        binary = shutil.which('ffmpeg')
        if not binary:
            return source

        def produce(target):
            args = [binary, '-nostdin', '-v', 'error', '-y', '-threads', '1', '-filter_threads', '1', '-i', str(source)]
            if quality == 'original':
                args += ['-map', '0', '-c', 'copy']
            else:
                args += ['-map', '0:v:0', '-map', '0:a?', '-vf', 'scale=min(360\\,iw):-2',
                         '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', '-maxrate', '450k',
                         '-bufsize', '900k', '-threads', '1', '-c:a', 'aac', '-b:a', '64k']
            args += ['-movflags', '+faststart', str(target)]
            from easel.workload_isolation import worker_command, worker_environment
            subprocess.run(worker_command(args, kind='media'), env=worker_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True, timeout=90 if quality == 'low' else 30)

        return self._variant(source, 'playback-' + quality, '.mp4', produce)

    def thumbnail(self, source: Path, *, width: int = 192) -> Path:
        from PIL import Image, ImageOps
        width = max(64, min(int(width), 640))

        def produce(target):
            with Image.open(source) as original:
                image = ImageOps.exif_transpose(original)
                image.thumbnail((width, width), Image.Resampling.LANCZOS)
                image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
                image.save(target, 'WEBP', quality=75, method=4)

        return self._variant(source, f'thumbnail-{width}', '.webp', produce)

    def response(self, request: Request, relative: str, *, actor_scope: str) -> Response:
        source = self.source(relative)
        target = source
        playback = request.query_params.get('playback')
        if playback:
            target = self.playback(source, quality='low' if playback == 'low' else 'original')
        elif request.query_params.get('thumb') == '1':
            target = self.thumbnail(source, width=int(request.query_params.get('w', '192')))
        info = target.stat()
        identity = f'{actor_scope}\n{target}\n{info.st_size}\n{info.st_mtime_ns}'
        etag = '"' + hashlib.sha256(identity.encode()).hexdigest() + '"'
        headers = {'Cache-Control': 'private, no-cache', 'ETag': etag,
                   'Vary': 'Cookie, X-Easel-User', 'X-Content-Type-Options': 'nosniff'}
        request.scope.setdefault('state', {})['validated_private_media'] = True
        validators = request.headers.get('if-none-match', '').split(',')
        if any(value.strip().removeprefix('W/') in {etag, '*'} for value in validators):
            return Response(status_code=304, headers=headers)
        return FileResponse(target, headers=headers, media_type=mimetypes.guess_type(str(target))[0], stat_result=info)
