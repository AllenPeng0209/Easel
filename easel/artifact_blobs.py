"""Private persistent content-addressed snapshots for D08 chat deliverables.

Python host adaptation of DeepSeek Harness's capture/wx/same-content semantics:
packages/deliverables/workspace-changes/src/capture.ts at
ce7073d51254c26cd63b22a60835b8f948deb447 (MIT, Copyright (c) 2026 DeepSeek).
The upstream notice is retained in integrations/deepseek-harness/LICENSE.

Streaming copy, flush/fsync and source-change checks reuse Xiangya's existing
video_workshop.py:deliver/_digest sequence. Unlike upstream's temporary SHA-1
captures, these SHA-256 blobs survive host restarts. Only already verified source
handles enter this module; it never reopens the source pathname.
"""
from __future__ import annotations

import errno
import hashlib
import io
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable


CHUNK_BYTES = 1024 * 1024
_DIGEST = re.compile(r'[a-f0-9]{64}')
_SECURE_DIR_FD = (hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY')
                  and os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd
                  and os.link in os.supports_dir_fd and os.unlink in os.supports_dir_fd)


class SnapshotError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class SnapshotCancelled(SnapshotError):
    def __init__(self):
        super().__init__('快照已取消，未确认交付版本', 499)


def _failure(error: OSError) -> SnapshotError:
    if error.errno == errno.ENOENT:
        return SnapshotError('快照数据或私有目录不存在', 404)
    if error.errno in (errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM):
        return SnapshotError('快照路径不安全或不可访问', 403)
    if error.errno in (getattr(errno, 'EOPNOTSUPP', -1), getattr(errno, 'ENOTSUP', -1), errno.EXDEV):
        return SnapshotError('当前文件系统不支持快照需要的原子文件操作', 501)
    return SnapshotError('快照存储不可用，未确认交付版本', 500)


def _identity(info) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _link_or_junction(info) -> bool:
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0)
                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))


def _source_facts(stream: BinaryIO):
    try:
        descriptor = stream.fileno()
    except (AttributeError, io.UnsupportedOperation):
        return None
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise SnapshotError('快照来源必须是已验证的普通文件', 403)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _source_length(stream: BinaryIO, facts) -> int:
    if facts is not None:
        return facts[2]
    if isinstance(stream, io.BytesIO):
        with stream.getbuffer() as buffer:
            return buffer.nbytes
    # BinaryIO sources without a native descriptor still need a finite initial
    # extent; never consume an unbounded producer under an immutable-file API.
    stream.seek(0, os.SEEK_END)
    length = stream.tell()
    stream.seek(0)
    if type(length) is not int or length < 0:
        raise SnapshotError('快照来源没有有效的初始大小', 400)
    return length


def _check_cancelled(cancelled: Callable[[], bool] | None):
    if cancelled is not None and cancelled():
        raise SnapshotCancelled()


class ArtifactBlobStore:
    """CAS files are named solely by SHA-256 inside one actor/IP private root.

    Directory-relative no-follow operations protect supported hosts. The
    portable path uses D01's lstat/junction/root identity and opened-file guards;
    it detects replacements but cannot promise atomic Windows reparse fencing
    without native directory-handle APIs.
    Source streams remain owned by the caller. Capture rewinds seekable input
    so an earlier inspection cannot silently turn a full file into a tail copy.
    """

    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        if len(self.root.parts) < 2 or any(part in ('.', '..') for part in self.root.parts[1:]):
            raise SnapshotError('快照私有目录无效', 400)
        self._root_identity = None
        with self._root_fd(create=True) as directory:
            if isinstance(directory, int):
                os.fchmod(directory, 0o700)
                info = os.fstat(directory)
            else:
                info = self.root.lstat()
            self._root_identity = _identity(info)

    def _portable_root(self, create: bool = False):
        current = Path(self.root.anchor)
        for part in self.root.parts[1:]:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                info = current.lstat()
            if _link_or_junction(info) or not stat.S_ISDIR(info.st_mode):
                raise SnapshotError('快照目录不能包含符号链接或重解析目录', 403)
        if self._root_identity is not None and _identity(info) != self._root_identity:
            raise SnapshotError('快照私有目录已变化，请重新尝试', 409)
        return info

    @contextmanager
    def _root_fd(self, *, create: bool = False):
        """Walk every root component without following a parent or root link."""
        directory = None
        try:
            if not _SECURE_DIR_FD:
                self._portable_root(create)
                yield self.root
                return
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
            directory = os.open(self.root.anchor, flags)
            for part in self.root.parts[1:]:
                try:
                    following = os.open(part, flags, dir_fd=directory)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, 0o700, dir_fd=directory)
                    except FileExistsError:
                        pass  # Another constructor may have created this level.
                    following = os.open(part, flags, dir_fd=directory)
                os.close(directory)
                directory = following
            info = os.fstat(directory)
            if self._root_identity is not None and _identity(info) != self._root_identity:
                raise SnapshotError('快照私有目录已变化，请重新尝试', 409)
            yield directory
        except OSError as error:
            raise _failure(error) from None
        finally:
            if directory is not None:
                os.close(directory)

    def _check_root(self):
        with self._root_fd():
            pass

    def _open_at(self, directory: int | Path, name: str, flags: int, mode: int = 0o600) -> int:
        if isinstance(directory, int):
            return os.open(name, flags, mode, dir_fd=directory)
        self._portable_root()
        path = directory / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None and (_link_or_junction(info) or not stat.S_ISREG(info.st_mode)):
            raise SnapshotError('快照文件不能是链接或非普通文件', 403)
        descriptor = os.open(path, flags, mode)
        try:
            actual = os.fstat(descriptor)
            after = path.lstat()
            self._portable_root()
            if (_link_or_junction(after) or not stat.S_ISREG(actual.st_mode)
                    or _identity(actual) != _identity(after)):
                raise SnapshotError('快照文件身份已变化', 409)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _publish(self, directory: int | Path, temporary: str, sha256: str):
        if isinstance(directory, int):
            os.link(temporary, sha256, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            return
        self._portable_root()
        info = (directory / temporary).lstat()
        if _link_or_junction(info) or not stat.S_ISREG(info.st_mode):
            raise SnapshotError('快照临时文件身份已变化', 409)
        try:
            os.link(directory / temporary, directory / sha256, follow_symlinks=False)
        except NotImplementedError:
            # Some platforms only expose the basic hard-link call. Both paths
            # remain private; the final file still receives full SHA validation.
            os.link(directory / temporary, directory / sha256)
        self._portable_root()

    def _sync_directory(self, directory: int | Path):
        if isinstance(directory, int):
            os.fsync(directory)
            return
        self._portable_root()
        try:
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
                                 | getattr(os, 'O_NOFOLLOW', 0))
        except OSError as error:
            # Python's normal Windows API cannot open a directory for fsync.
            # File data was fsynced before the atomic NTFS hard-link creation;
            # native directory FlushFileBuffers durability is not claimed.
            if os.name == 'nt' and error.errno in (errno.EACCES, errno.EPERM, errno.EISDIR):
                return
            raise
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or _identity(info) != self._root_identity:
                raise SnapshotError('快照私有目录已变化', 409)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._portable_root()

    @staticmethod
    def _unlink_temporary(directory: int | Path, name: str, expected_identity):
        if isinstance(directory, int):
            os.unlink(name, dir_fd=directory)
            return
        path = directory / name
        info = path.lstat()
        # Never remove a replacement entry belonging to someone else.
        if expected_identity is not None and _identity(info) != expected_identity:
            return
        path.unlink()

    @staticmethod
    def _validate_receipt(sha256: str, size: int):
        if (not isinstance(sha256, str) or not _DIGEST.fullmatch(sha256)
                or type(size) is not int or size < 0):
            raise SnapshotError('快照摘要或大小无效', 400)

    def _verified_file(self, directory: int | Path, sha256: str, size: int, *, expected_identity=None) -> BinaryIO:
        descriptor = None
        stream = None
        try:
            descriptor = self._open_at(directory, sha256, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                                       | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0)
                                       | getattr(os, 'O_BINARY', 0))
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise SnapshotError('快照不是普通文件', 403)
            if expected_identity is not None and _identity(before) != expected_identity:
                raise SnapshotError('快照临时文件身份已变化', 409)
            if before.st_size != size:
                raise SnapshotError('快照大小校验失败，文件可能已损坏', 409)
            stream = os.fdopen(descriptor, 'rb')
            descriptor = None
            digest = hashlib.sha256()
            consumed = 0
            while True:
                chunk = stream.read(min(CHUNK_BYTES, size - consumed + 1))
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > size:
                    raise SnapshotError('快照大小在校验期间发生变化', 409)
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            # link/unlink of another publisher's private temp can change ctime
            # without changing bytes; verify content, inode, size and mtime.
            if (digest.hexdigest() != sha256 or _identity(after) != _identity(before)
                    or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns):
                raise SnapshotError('快照摘要校验失败，文件可能已损坏', 409)
            stream.seek(0)
            return stream
        except BaseException:
            if stream is not None:
                stream.close()
            if descriptor is not None:
                os.close(descriptor)
            raise

    def capture(self, stream: BinaryIO, *, max_bytes: int | None = None,
                cancelled: Callable[[], bool] | None = None) -> dict:
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
            raise SnapshotError('快照大小限制无效', 400)
        if cancelled is not None and not callable(cancelled):
            raise SnapshotError('快照取消回调无效', 400)
        _check_cancelled(cancelled)
        temporary = '.tmp-' + uuid.uuid4().hex
        with self._root_fd() as directory:
            descriptor = None
            created = False
            temp_identity = None
            try:
                stream.seek(0)
                before = _source_facts(stream)
                source_length = _source_length(stream, before)
                if max_bytes is not None and source_length > max_bytes:
                    raise SnapshotError('快照资源超过大小限制', 413)
                descriptor = self._open_at(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                           | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
                                           | getattr(os, 'O_BINARY', 0))
                created = True
                temp_identity = _identity(os.fstat(descriptor))
                outgoing = os.fdopen(descriptor, 'wb')
                descriptor = None
                digest = hashlib.sha256()
                size = 0
                with outgoing:
                    while True:
                        _check_cancelled(cancelled)
                        # An appending source cannot keep us chasing EOF. A
                        # single probe byte beyond the original extent detects
                        # growth while bounding all I/O and temporary disk use.
                        amount = min(CHUNK_BYTES, source_length - size + 1)
                        if max_bytes is not None:
                            amount = min(amount, max_bytes - size + 1)
                        chunk = stream.read(amount)
                        if not isinstance(chunk, (bytes, bytearray, memoryview)):
                            raise SnapshotError('快照来源不是二进制文件', 400)
                        _check_cancelled(cancelled)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > source_length:
                            raise SnapshotError('源文件在快照期间增长，请重新声明', 409)
                        if max_bytes is not None and size > max_bytes:
                            raise SnapshotError('快照资源超过大小限制', 413)
                        outgoing.write(chunk)
                        digest.update(chunk)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                    temp_identity = _identity(os.fstat(outgoing.fileno()))
                after = _source_facts(stream)
                if before != after or size != source_length:
                    raise SnapshotError('源文件在快照期间发生变化，请重新声明', 409)
                _check_cancelled(cancelled)
                self._check_root()
                sha256 = digest.hexdigest()
                published = False
                try:
                    # link creates the final name atomically and never replaces
                    # an existing blob. The temp is already complete and fsynced.
                    self._publish(directory, temporary, sha256)
                    published = True
                except FileExistsError:
                    pass
                # An EEXIST may be another in-flight publisher's freshly linked
                # file. Each accepted receipt confirms the directory barrier.
                self._sync_directory(directory)
                with self._verified_file(directory, sha256, size,
                                         expected_identity=temp_identity if published else None):
                    pass
                self._check_root()
                _check_cancelled(cancelled)
                return {'sha256': sha256, 'size': size}
            except SnapshotError:
                raise
            except NotImplementedError:
                raise SnapshotError('当前文件系统不支持原子快照发布，请使用支持硬链接的私有存储目录', 501) from None
            except (OSError, ValueError, TypeError) as error:
                if isinstance(error, OSError):
                    raise _failure(error) from None
                raise SnapshotError('快照来源无法读取或定位', 400) from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if created:
                    try:
                        self._unlink_temporary(directory, temporary, temp_identity)
                    except FileNotFoundError:
                        pass

    def capture_bytes(self, data: bytes, *, max_bytes: int | None = None,
                      cancelled: Callable[[], bool] | None = None) -> dict:
        if not isinstance(data, bytes):
            raise SnapshotError('快照资源必须是二进制内容', 400)
        return self.capture(io.BytesIO(data), max_bytes=max_bytes, cancelled=cancelled)

    def open_blob(self, sha256: str, size: int) -> BinaryIO:
        self._validate_receipt(sha256, size)
        with self._root_fd() as directory:
            stream = None
            try:
                stream = self._verified_file(directory, sha256, size)
                self._check_root()
                return stream
            except BaseException:
                if stream is not None:
                    stream.close()
                raise
