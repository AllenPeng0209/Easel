"""Read preview members from one already-authorized immutable ZIP descriptor.

The media suffix/name/count rules are migrated from web.app.api_artifact_archive;
the HTML dependency contract follows DeepSeek ui-sidebar-documentpreview HTML
read-relative/pack (MIT, ce7073d). No member is extracted to the filesystem and
no current source file is consulted. The caller must authorize and verify the
snapshot descriptor before transferring its ownership to this reader.

Indexing only inspects metadata, not media bodies. A requested member is checked
through EOF before it is returned, so a corrupt CRC cannot become a successful
content response. Successful checks are cached for the reader's immutable blob.
Normal media is streamed, not subject to the 4 MiB HTML-resource limit or a
whole-archive byte cap. Extreme expansion (>8 MiB and >2000:1) is rejected before
decompression. The inherited count boundaries are 1000 entries / 100 media
previews. Other safe members retain fixed-version downloads without consuming
the media budget. Duplicate names are explicitly addressable by original index;
path-based HTML dependencies reject ambiguity rather than guessing a version.
"""
from __future__ import annotations

from dataclasses import dataclass
import mimetypes
import posixpath
import re
import stat
from typing import BinaryIO
import zipfile
import zlib

from easel.chat_artifacts import ArtifactError

MAX_ARCHIVE_ENTRIES = 1000
MAX_PREVIEW_MEMBERS = 100
MAX_RELATED_BYTES = 4 * 1024 * 1024
EXPANSION_CHECK_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION_RATIO = 2000
_CHUNK_BYTES = 64 * 1024
_KINDS = {
    **dict.fromkeys(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.avif', '.bmp', '.ico'), 'image'),
    **dict.fromkeys(('.html', '.htm'), 'html'),
    **dict.fromkeys(('.mp4', '.webm', '.mov', '.m4v'), 'video'),
    **dict.fromkeys(('.mp3', '.wav', '.ogg', '.m4a', '.flac'), 'audio'),
    '.pdf': 'pdf',
}
_RELATED_SUFFIXES = {'.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.ico', '.svg'}
_PRIVATE_NAMES = {'auth.json', 'cookies.json', 'cookies.txt', 'credentials', 'credentials.json',
    'credentials.yaml', 'credentials.yml', 'credentials.toml', 'secrets.json',
    'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'}
_PRIVATE_STEMS = {'credentials', 'secrets', 'secret', 'cookies', 'api_key', 'api-key',
    'access_token', 'access-token', 'refresh_token', 'refresh-token', 'password', 'passwords'}
_COMPRESSION_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
if zipfile.bz2 is not None:
    _COMPRESSION_METHODS.add(zipfile.ZIP_BZIP2)
if zipfile.lzma is not None:
    _COMPRESSION_METHODS.add(zipfile.ZIP_LZMA)
_READ_ERRORS = (OSError, zipfile.BadZipFile, EOFError, ValueError, zlib.error)
if zipfile.lzma is not None:
    _READ_ERRORS += (zipfile.lzma.LZMAError,)


class ArchiveSnapshotError(ArtifactError):
    def __init__(self, message: str, status_code: int = 400, code: str = 'invalid_archive'):
        super().__init__(message, status_code)
        self.code = code


def _suffix(path: str) -> str:
    return posixpath.splitext(path)[1].lower()


def _safe_name(path: str) -> bool:
    if (not isinstance(path, str) or not path or path.startswith('/') or '\\' in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', path)):
        return False
    for part in path.split('/'):
        lower = part.lower()
        if (not part or part.startswith(('.', '_')) or ':' in part
                or lower in _PRIVATE_NAMES or posixpath.splitext(lower)[0] in _PRIVATE_STEMS
                or lower.endswith(('.pem', '.key', '.p12', '.pfx'))):
            return False
    return True


def _regular(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return not info.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG)


def _mime(path: str) -> str:
    return {'.js': 'text/javascript', '.css': 'text/css'}.get(_suffix(path)) or mimetypes.guess_type(path)[0] or 'application/octet-stream'


@dataclass
class ArchiveMember:
    stream: BinaryIO
    size: int
    mime: str
    name: str
    path: str
    crc32: str

    def close(self) -> None:
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class ArchiveSnapshotReader:
    """Own a verified snapshot FD; closing also closes all opened member streams."""
    def __init__(self, blob: BinaryIO):
        self._blob = blob
        self._archive: zipfile.ZipFile | None = None
        self._members: list[BinaryIO] = []
        self._by_path: dict[str, list[tuple[int, zipfile.ZipInfo]]] = {}
        self._path_occurrences: dict[str, int] = {}
        self._visible: dict[int, zipfile.ZipInfo] = {}
        self._verified: set[int] = set()
        self._closed = False
        try:
            self._archive = zipfile.ZipFile(blob)
            entries = self._archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ArchiveSnapshotError('压缩包文件过多，暂不自动预览', 413, 'too_many_members')
            blocked_prefixes = set()
            for entry in entries:
                name = entry.filename.rstrip('/')
                if entry.orig_filename == entry.filename and _safe_name(entry.filename) and not entry.is_dir():
                    self._path_occurrences[name] = self._path_occurrences.get(name, 0) + 1
                if not _regular(entry) and not entry.is_dir():
                    blocked_prefixes.add(name)
                if (entry.file_size > EXPANSION_CHECK_BYTES
                        and entry.file_size > max(1, entry.compress_size) * MAX_COMPRESSION_RATIO):
                    raise ArchiveSnapshotError('ZIP 成员解压压缩比异常，无法安全预览', 413, 'expansion_ratio')
            media_count = 0
            for index, entry in enumerate(entries):
                name = entry.filename
                if (entry.orig_filename != name or not _safe_name(name) or not _regular(entry)
                        or entry.flag_bits & 1 or entry.compress_type not in _COMPRESSION_METHODS
                        or any(name == prefix or name.startswith(prefix + '/') for prefix in blocked_prefixes)):
                    continue
                self._by_path.setdefault(name, []).append((index, entry))
                if _suffix(name) not in _KINDS:
                    self._visible[index] = entry
                elif media_count < MAX_PREVIEW_MEMBERS:
                    self._visible[index] = entry
                    media_count += 1
        except ArchiveSnapshotError:
            self.close()
            raise
        except (OSError, ValueError, zipfile.BadZipFile, EOFError) as error:
            self.close()
            raise ArchiveSnapshotError('无法读取 ZIP 压缩包索引', 400, 'corrupt_archive') from error

    def _ensure_open(self) -> zipfile.ZipFile:
        if self._closed or self._archive is None:
            raise ArchiveSnapshotError('ZIP 预览已关闭', 404, 'closed_archive')
        return self._archive

    def _preview(self, index: int) -> zipfile.ZipInfo:
        self._ensure_open()
        if isinstance(index, bool) or not isinstance(index, int) or index not in self._visible:
            raise ArchiveSnapshotError('ZIP 成员不存在或不可预览', 404, 'member_not_found')
        return self._visible[index]

    def _open(self, info: zipfile.ZipInfo) -> BinaryIO:
        if info.flag_bits & 1:
            raise ArchiveSnapshotError('加密 ZIP 成员不能直接预览', 415, 'encrypted_member')
        try:
            return self._ensure_open().open(info, 'r')
        except (NotImplementedError, RuntimeError) as error:
            raise ArchiveSnapshotError('ZIP 成员压缩方式不支持', 415, 'unsupported_compression') from error
        except (OSError, zipfile.BadZipFile, EOFError, ValueError) as error:
            raise ArchiveSnapshotError('ZIP 成员已损坏，无法读取', 400, 'corrupt_member') from error

    def _read(self, stream: BinaryIO, size: int) -> bytes:
        try:
            return stream.read(size)
        except _READ_ERRORS as error:
            raise ArchiveSnapshotError('ZIP 成员 CRC 校验失败或内容损坏', 400, 'corrupt_member') from error

    def _verify(self, index: int, info: zipfile.ZipInfo) -> None:
        if index in self._verified:
            return
        count = 0
        with self._open(info) as stream:
            while chunk := self._read(stream, _CHUNK_BYTES):
                count += len(chunk)
        if count != info.file_size:
            raise ArchiveSnapshotError('ZIP 成员大小与记录不一致，内容损坏', 400, 'corrupt_member')
        self._verified.add(index)

    def index(self) -> list[dict]:
        self._ensure_open()
        return [{'index': index, 'name': posixpath.basename(info.filename), 'path': info.filename,
                 'mime': _mime(info.filename), 'size': info.file_size, 'kind': _KINDS.get(_suffix(info.filename), 'file'),
                 'crc32': f'{info.CRC:08x}', 'crcVerified': index in self._verified}
                for index, info in self._visible.items()]

    def open_member(self, index: int) -> ArchiveMember:
        info = self._preview(index)
        self._verify(index, info)
        stream = self._open(info)
        self._members.append(stream)
        return ArchiveMember(stream, info.file_size, _mime(info.filename), posixpath.basename(info.filename),
                             info.filename, f'{info.CRC:08x}')

    def read_related(self, index: int, path: str) -> tuple[bytes, str]:
        """Resolve an already-decoded HTML dependency inside the ZIP, without unquoting again."""
        document = self._preview(index)
        if _KINDS.get(_suffix(document.filename)) != 'html':
            raise ArchiveSnapshotError('相关资源必须属于 HTML 成员', 415, 'not_html')
        if (not isinstance(path, str) or not path or path.startswith('/') or '\\' in path
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', path)):
            raise ArchiveSnapshotError('HTML 资源相对路径无效', 403, 'unsafe_path')
        combined = posixpath.normpath(posixpath.join(posixpath.dirname(document.filename), path))
        if not _safe_name(combined):
            raise ArchiveSnapshotError('HTML 资源不能读取 ZIP 外部或私有内容', 403, 'unsafe_path')
        if _suffix(combined) not in _RELATED_SUFFIXES:
            raise ArchiveSnapshotError('HTML 预览只读取静态图片、CSS 和经典 JavaScript', 415, 'unsupported_resource')
        matches = self._by_path.get(combined)
        if not matches:
            raise ArchiveSnapshotError('ZIP 中不存在该 HTML 资源', 404, 'member_not_found')
        if len(matches) != 1 or self._path_occurrences.get(combined, 0) > 1:
            raise ArchiveSnapshotError('ZIP 相关资源名称重复，无法消除版本歧义', 409, 'ambiguous_member')
        member_index, info = matches[0]
        if info.file_size > MAX_RELATED_BYTES:
            raise ArchiveSnapshotError('HTML 单个资源不能超过 4 MiB', 413, 'resource_too_large')
        with self._open(info) as stream:
            content = self._read(stream, MAX_RELATED_BYTES + 1)
        if len(content) > MAX_RELATED_BYTES:
            raise ArchiveSnapshotError('HTML 单个资源不能超过 4 MiB', 413, 'resource_too_large')
        if len(content) != info.file_size:
            raise ArchiveSnapshotError('ZIP 成员大小与记录不一致，内容损坏', 400, 'corrupt_member')
        self._verified.add(member_index)
        return content, _mime(info.filename)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            for member in self._members:
                member.close()
            if self._archive is not None:
                self._archive.close()
        finally:
            self._blob.close()

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_exc):
        self.close()
