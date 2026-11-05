"""Current-IP immutable reference evidence for native H3 production.

No function in this module submits a generation. Local files are examined as
bytes; a model-provided filename, media kind, digest, or duration is not proof.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile

from PIL import Image

from easel.video_workshop import WorkshopError


REF11_MODEL = 'easel-ppu-h3-ref11-local::minimax-h3-ref2va-ref11-8step'
TEXT_MODELS = {'easel-ppu-h3-local::minimax-h3-dense8', 'easel-ppu-h3-local::minimax-h3-base'}
_MAX_BYTES = {'image': 10 * 1024 * 1024, 'video': 50 * 1024 * 1024, 'audio': 15 * 1024 * 1024}
_IMAGE_MIMES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}
_AV_MIMES = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
             '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.flac': 'audio/flac',
             '.ogg': 'audio/ogg', '.aac': 'audio/aac'}
_DEMUXERS = {'.mp4': 'mov', '.mov': 'mov', '.m4a': 'mov', '.webm': 'matroska',
             '.wav': 'wav', '.mp3': 'mp3', '.flac': 'flac', '.ogg': 'ogg', '.aac': 'aac'}


def _reject(message: str, status=422):
    raise WorkshopError(message, status)


def _relative(value) -> PurePosixPath:
    if (not isinstance(value, str) or not 1 <= len(value) <= 512 or '\\' in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _reject('参考路径必须是当前 IP 内容库中的相对文件路径')
    parts = value.split('/')
    path = PurePosixPath(value)
    if (path.is_absolute() or len(parts) < 2 or any(part in ('', '.', '..') for part in parts)
            or any(part.startswith(('.', '_')) for part in parts) or ':' in parts[0]):
        _reject('参考文件不能来自外部地址、隐藏目录或其他 IP')
    return path


def _read_bytes(outputs: Path, relative: PurePosixPath, maximum: int) -> bytes:
    """Walk with directory FDs: a swapped symlink must not escape the IP root."""
    outputs = Path(outputs)
    descriptors = []
    try:
        descriptor = os.open(outputs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        for part in relative.parts[:-1]:
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            descriptors.append(descriptor)
        file_descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            _reject('参考文件为空、类型不正确或超过本模型限制')
        with os.fdopen(os.dup(file_descriptor), 'rb') as source:
            content = source.read(maximum + 1)
        after = os.fstat(file_descriptor)
        if (not 0 < len(content) <= maximum or len(content) != before.st_size
                or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
            _reject('参考文件读取期间发生变化，尚未提交生成', 409)
        return content
    except (OSError, ValueError):
        _reject('参考文件不可用或路径经过符号链接，尚未提交生成', 403)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _probe(content: bytes, kind: str, suffix: str) -> dict:
    if kind == 'image':
        try:
            with Image.open(BytesIO(content)) as image:
                width, height, image_format = image.width, image.height, image.format
                if (image_format not in _IMAGE_MIMES or not 0 < width <= 8192 or not 0 < height <= 8192
                        or width * height > 32 * 1024 * 1024):
                    raise ValueError
                image.verify()
            return {'mimeType': _IMAGE_MIMES[image_format], 'width': width, 'height': height, 'hasAudio': False}
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            _reject('参考图片未通过真实栅格图片与尺寸校验')
    mime = _AV_MIMES.get(suffix.lower(), '')
    if not mime.startswith(kind + '/'):
        _reject('参考文件格式与声明的音视频类型不一致')
    try:
        # A seekable, unlinked FD keeps the probe on the exact bytes that were
        # hashed, without exposing a cross-workspace temporary pathname. Force
        # the expected demuxer so a disguised playlist cannot open more files.
        with tempfile.TemporaryFile() as probe_input:
            probe_input.write(content)
            probe_input.flush()
            process = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-f', _DEMUXERS[suffix.lower()], '-show_entries',
                'format=duration,format_name:stream=codec_type,width,height', '-of', 'json',
                '-i', f'/dev/fd/{probe_input.fileno()}'],
                pass_fds=(probe_input.fileno(),), capture_output=True, check=True, timeout=30)
        data = json.loads(process.stdout)
        duration = float(data['format']['duration'])
        streams = data['streams']
        audio = any(item.get('codec_type') == 'audio' for item in streams)
        if not math.isfinite(duration) or not 0 < duration <= 15:
            raise ValueError
        result = {'mimeType': mime, 'durationMs': round(duration * 1000), 'hasAudio': audio}
        if kind == 'video':
            video = next(item for item in streams if item.get('codec_type') == 'video')
            if not 0 < video['width'] <= 8192 or not 0 < video['height'] <= 8192:
                raise ValueError
            result.update(width=video['width'], height=video['height'])
        elif not audio:
            raise ValueError
        return result
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError):
        _reject('参考音视频未通过真实媒体与 15 秒时长校验')


def freeze_references(outputs: Path, references: object) -> list[dict]:
    """Accept only kind/path inputs, and derive all media claims from real bytes."""
    if not isinstance(references, list) or len(references) > 12:
        _reject('单片最多支持 12 项已选择的参考')
    result, seen = [], set()
    for item in references:
        if not isinstance(item, dict) or set(item) != {'kind', 'path'} or item.get('kind') not in _MAX_BYTES:
            _reject('参考输入仅接受媒体类型与当前 IP 相对路径，不接受自报摘要或时长')
        kind, path = item['kind'], _relative(item['path'])
        if item['path'] in seen:
            _reject('同一片段的参考文件不能重复')
        seen.add(item['path'])
        content = _read_bytes(outputs, path, _MAX_BYTES[kind])
        metadata = _probe(content, kind, path.suffix)
        result.append({'kind': kind, 'path': path.as_posix(), 'sha256': hashlib.sha256(content).hexdigest(),
                       'bytes': len(content), **metadata})
    return result


def verify_references(outputs: Path, references: object) -> list[Path]:
    """A modified approved file is a refusal, never a silent reference upgrade."""
    if not isinstance(references, list) or len(references) > 12:
        _reject('原参考清单无效', 409)
    paths = []
    for item in references:
        if (not isinstance(item, dict) or item.get('kind') not in _MAX_BYTES
                or not isinstance(item.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])
                or type(item.get('bytes')) is not int):
            _reject('原参考证据未冻结', 409)
        path = _relative(item.get('path'))
        content = _read_bytes(outputs, path, _MAX_BYTES[item['kind']])
        if len(content) != item['bytes'] or hashlib.sha256(content).hexdigest() != item['sha256']:
            _reject('已批准的参考文件发生变化，尚未提交生成', 409)
        paths.append(Path(outputs) / path)
    return paths


def validate_reference_spec(model_id: str, seconds: int, aspect_ratio: str, references: list[dict]) -> None:
    if type(seconds) is not int or not 1 <= seconds <= 15 or not isinstance(references, list):
        _reject('视频请求时长或参考清单无效')
    counts = Counter(item.get('kind') for item in references if isinstance(item, dict))
    if sum(counts.values()) != len(references) or set(counts) - set(_MAX_BYTES):
        _reject('视频参考类型无效')
    if model_id == REF11_MODEL:
        if (seconds > 14 or aspect_ratio not in {'9:16', '16:9'} or not counts['image'] + counts['video']
                or counts['image'] > 9 or counts['video'] > 3 or counts['audio'] > 3
                or len(references) > 12 or counts['video'] + counts['audio'] > 3):
            _reject('Ref11 需图片或视频参考，限 1–14 秒及横竖两种画幅；当前适配器最多 3 段音视频参考')
    elif model_id in TEXT_MODELS or 'pika-h3' in model_id:
        if counts['video'] or counts['audio'] or counts['image'] > 1:
            _reject('当前普通 H3 通道仅支持纯文字或一张图片参考')
    elif references:
        _reject('该视频通道尚未提供可验证的参考输入契约')
