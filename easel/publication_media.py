"""Bind publication subjects to D08 snapshots and stage their approved bytes.

The publisher still receives ordinary image/video paths. Those paths are private
task copies of a verified snapshot, never paths back to mutable content outputs.
ArtifactBlobStore supplies D08's existing bounded copy, hash, fsync, no-follow
directory walk and atomic publication; this module adds no second version store.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import threading

from easel.artifact_blobs import ArtifactBlobStore, SnapshotError
from easel.chat_artifacts import ArtifactError, ChatArtifactStore


# Keep this adapter aligned with the current automatic publisher's suffixes.
_MEDIA_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg',
                   '.mp4', '.mov', '.webm', '.m4v'}
_FIELDS = ('artifactId', 'path', 'name', 'mime', 'revision', 'sha256',
           'bundleSha256', 'size', 'url')
_ALLOWED_FIELDS = set(_FIELDS) | {'workspacePath'}
_ID = re.compile(r'[a-f0-9]{32}')
_SHA = re.compile(r'[a-f0-9]{64}')


class StagedPublicationMedia(list):
    """List-compatible staged paths with explicit late-result cleanup ownership."""
    def __init__(self, paths, cleanup):
        super().__init__(paths)
        self._cleanup = cleanup

    def cleanup(self) -> bool:
        return self._cleanup()


def _fail(message: str, status: int = 409):
    raise ArtifactError(message, status)


def _media_view(store: ChatArtifactStore, record: dict) -> dict:
    if not isinstance(record, dict) or record.get('storageMode') != 'snapshot':
        _fail('发布媒体必须是已封存的固定交付版本')
    try:
        result = {key: record[key] for key in _FIELDS}
        path = Path(result['path'])
        if (record.get('projectId') != store.project_id
                or not isinstance(result['artifactId'], str) or not _ID.fullmatch(result['artifactId'])
                or not isinstance(result['path'], str) or path.is_absolute()
                or not path.parts or any(part.startswith('.') or '\\' in part for part in path.parts)
                or result['name'] != path.name
                or not isinstance(result['mime'], str)
                or not result['mime'].startswith(('image/', 'video/'))
                or path.suffix.lower() not in _MEDIA_SUFFIXES
                or type(result['revision']) is not int or result['revision'] < 1
                or not isinstance(result['sha256'], str) or not _SHA.fullmatch(result['sha256'])
                or type(result['size']) is not int or result['size'] < 0
                or result['bundleSha256'] is not None
                or result['url'] != f"/api/chat/artifacts/{result['artifactId']}/content"):
            _fail('发布媒体的固定版本信息无效')
        result['workspacePath'] = (Path('outputs') / path).as_posix()
        if record.get('workspacePath') != result['workspacePath']:
            _fail('发布媒体的工作区路径信息无效')
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ArtifactError):
            raise
        _fail('发布媒体的固定版本信息损坏')
    return result


def _list(value, label):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _fail(f'{label}必须是有效的字符串列表', 400)
    return value


def _source_path(store: ChatArtifactStore, value: str) -> str:
    if ('\\' in value or any(ord(char) < 32 for char in value)
            or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', value)):
        _fail('发布媒体路径无效', 400)
    path = Path(value)
    if path.is_absolute():
        return value
    # PublishPage uses output-relative coordinates; native present uses cwd-
    # relative coordinates. Anchor both accepted input spellings to this store.
    parts = path.parts[1:] if path.parts and path.parts[0] == 'outputs' else path.parts
    return str(store.outputs.joinpath(*parts))


def capture_publication_media(store: ChatArtifactStore, *, paths: list[str] | None = None,
                              artifact_ids: list[str] | None = None,
                              session_id: str, turn_id: str, call_id: str) -> list[dict]:
    """Return canonical media references scoped to the supplied actor/IP store.

    Paths accept PublishPage output-relative or ``outputs/`` coordinates. Native
    IDs reuse existing snapshots. Per-batch D08 receipts make retries stable;
    callers persist an approved publication subject only after this returns.
    """
    paths, artifact_ids = _list(paths, '媒体路径'), _list(artifact_ids, '交付版本 ID')
    if paths and artifact_ids:
        _fail('媒体路径与固定交付版本 ID 只能选择一种', 400)
    if not paths and not artifact_ids:
        return []
    if not all(isinstance(value, str) and value for value in (session_id, turn_id, call_id)):
        _fail('发布媒体需要有效的会话、轮次和调用标识', 400)
    if artifact_ids:
        result = []
        for identifier in artifact_ids:
            record = store.get(identifier)
            if record is None:
                _fail('固定交付版本不存在或不属于当前用户和 IP', 404)
            media = _media_view(store, record)
            store.verify_available(identifier)
            result.append(media)
        return result
    sources = [_source_path(store, value) for value in paths]
    # Validate coordinates before committing the first upstream batch. Leave
    # source opens to present: an idempotent retry must still return its accepted
    # snapshot if the original source has since been deleted.
    for source in sources:
        relative = store._relative(source, None)
        if relative.suffix.lower() not in _MEDIA_SUFFIXES:
            _fail('发布仅支持当前平台可处理的图片或视频', 415)
    result = []
    for start in range(0, len(sources), 8):
        records = store.present([{'path': source} for source in sources[start:start + 8]],
            session_id=session_id, turn_id=turn_id, call_id=f'{call_id}:media-batch:{start // 8}')
        result.extend(_media_view(store, record) for record in records)
    return result


def _stat_entry(directory, name):
    try:
        return (os.stat(name, dir_fd=directory, follow_symlinks=False) if isinstance(directory, int)
                else (directory / name).lstat())
    except FileNotFoundError:
        return None


def _identity(info):
    return info.st_dev, info.st_ino


def _unlink_owned(directory, name, identity):
    info = _stat_entry(directory, name)
    if info is None or _identity(info) != identity:
        return
    if isinstance(directory, int):
        os.unlink(name, dir_fd=directory)
    else:
        (directory / name).unlink()


def _cancelled(callback):
    if callback is not None and callback():
        _fail('发布媒体暂存已取消', 499)


def _cleanup_capability(guard, owned):
    files = tuple(owned.items())
    lock = threading.Lock()
    def cleanup():
        # Reopen through the original directory identity. A renamed/replaced
        # root or file is not authority to delete the new host-owned entry.
        with lock:
            try:
                with guard._root_fd() as target:
                    complete = True
                    for name, identity in files:
                        info = _stat_entry(target, name)
                        if info is None:
                            continue
                        if _identity(info) != identity:
                            complete = False
                            continue
                        _unlink_owned(target, name, identity)
                    guard._sync_directory(target)
                    return complete
            except (SnapshotError, OSError):
                return False
    return cleanup


def stage_publication_media(store: ChatArtifactStore, media: list[dict], directory: Path,
                            *, cancelled=None) -> list[str]:
    """Copy an approved subject into a host-generated private task directory.

    The caller owns successful returned files. Failure/cancellation removes this
    call's staged files, preserving pre-existing host state and replacement roots.
    ``directory`` is a host capability and must never come from a browser path.
    """
    if not isinstance(media, list):
        _fail('发布媒体必须是固定版本列表', 400)
    if cancelled is not None and not callable(cancelled):
        _fail('媒体暂存取消回调无效', 400)
    if not media:
        return StagedPublicationMedia([], lambda: True)
    selected = []
    for entry in media:
        if not isinstance(entry, dict) or set(entry) - _ALLOWED_FIELDS or set(_FIELDS) - set(entry):
            _fail('发布媒体的批准版本信息无效')
        record = store.get(entry.get('artifactId'))
        if record is None:
            _fail('固定交付版本不存在或不属于当前用户和 IP', 404)
        canonical = _media_view(store, record)
        if any(type(entry[key]) is not type(canonical[key]) or entry[key] != canonical[key] for key in entry):
            _fail('发布媒体已偏离批准的固定版本')
        selected.append(canonical)
    _cancelled(cancelled)
    root = Path(directory)
    if not root.is_absolute():
        _fail('发布媒体暂存必须使用服务器生成的绝对目录', 400)
    try:
        guard = ArtifactBlobStore(root)
        with guard._root_fd() as target:
            initial = set(os.listdir(target))
            created = {}
            candidates = set()
            lock_name = '.publication-stage.lock'
            lock_fd = None
            try:
                try:
                    lock_fd = guard._open_at(target, lock_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
                except FileExistsError:
                    _fail('发布媒体暂存目录已被占用，不会覆盖已有任务文件', 409)
                created[lock_name] = _identity(os.fstat(lock_fd))
                os.close(lock_fd)
                lock_fd = None
                results = []
                for index, entry in enumerate(selected):
                    _cancelled(cancelled)
                    candidates.add(entry['sha256'])
                    with store.open_content(entry['artifactId']) as stream:
                        receipt = guard.capture(stream, max_bytes=entry['size'], cancelled=cancelled)
                    candidates.add(receipt['sha256'])
                    info = _stat_entry(target, receipt['sha256'])
                    if info is None or not stat.S_ISREG(info.st_mode):
                        _fail('发布媒体暂存文件丢失或不安全')
                    if receipt['sha256'] not in initial and info is not None:
                        created[receipt['sha256']] = _identity(info)
                    if receipt != {'sha256':entry['sha256'], 'size':entry['size']}:
                        _fail('暂存媒体的字节与批准版本不一致')
                    suffix = Path(entry['name']).suffix.lower()
                    name = f"{index + 1:04d}-{entry['artifactId']}{suffix}"
                    if _stat_entry(target, name) is not None:
                        _fail('发布任务已有同名暂存文件，不会覆盖', 409)
                    # The CAS helper already copied and fsynced the complete
                    # bytes. Link that verified inode under a publisher suffix.
                    created[name] = _identity(info)
                    guard._publish(target, receipt['sha256'], name)
                    guard._sync_directory(target)
                    with guard._verified_file(target, receipt['sha256'], receipt['size']) as verified:
                        final = _stat_entry(target, name)
                        if (final is None or not stat.S_ISREG(final.st_mode)
                                or _identity(final) != _identity(os.fstat(verified.fileno()))):
                            _fail('发布媒体暂存文件身份已变化')
                    guard._check_root()
                    if receipt['sha256'] in created:
                        _unlink_owned(target, receipt['sha256'], created.pop(receipt['sha256']))
                    results.append(str(root / name))
                _cancelled(cancelled)
                guard._check_root()
                _unlink_owned(target, lock_name, created.pop(lock_name))
                guard._sync_directory(target)
                return StagedPublicationMedia(results, _cleanup_capability(guard, created))
            except BaseException:
                # A capture can publish a fully verified private CAS copy and
                # then be cancelled. Such a late copy still belongs to this job.
                for name in candidates - initial:
                    info = _stat_entry(target, name)
                    if info is not None and stat.S_ISREG(info.st_mode):
                        created.setdefault(name, _identity(info))
                for name, identity in reversed(list(created.items())):
                    _unlink_owned(target, name, identity)
                raise
            finally:
                if lock_fd is not None:
                    os.close(lock_fd)
    except SnapshotError as error:
        raise ArtifactError(str(error), error.status_code) from None
    except OSError:
        raise ArtifactError('发布媒体暂存失败，未确认任何上传或发布', 500) from None
