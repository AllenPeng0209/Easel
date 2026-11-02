"""D01 explicit deliverables, adapted from DeepSeek Harness tool-present (MIT).

Upstream: packages/deliverables/tool-present/src/index.ts and src/types.ts,
ce7073d51254c26cd63b22a60835b8f948deb447. Copyright (c) 2026 DeepSeek.
The original notice is retained in integrations/deepseek-harness/LICENSE.

Validate the complete files batch, then atomically record successful declarations.
Unlike the generic upstream Host, Easel accepts only the selected IP's approved
outputs. These are references to CURRENT files; D01 does not snapshot file bytes.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import posixpath
import re
import sqlite3
import stat
import time
from pathlib import Path
from typing import BinaryIO


class ArtifactError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


_CREDENTIAL_NAMES = {
    'auth.json', 'cookies.json', 'cookies.txt', 'credentials', 'credentials.json',
    'credentials.yaml', 'credentials.yml', 'credentials.toml', 'secrets.json',
    'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519',
}
_CREDENTIAL_STEMS = {'credentials', 'secrets', 'secret', 'cookies', 'api_key', 'api-key',
                     'access_token', 'access-token', 'refresh_token', 'refresh-token', 'password', 'passwords'}
_RELATED_EXTENSIONS = {'.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.ico', '.svg'}
# Same per-asset limit as upstream ui-sidebar-documentpreview/src/client/html/pack.ts.
MAX_RELATED_BYTES = 4 * 1024 * 1024


class ChatArtifactStore:
    def __init__(self, sessions: Path, outputs: Path, workspace: Path, project_id: str):
        self.directory = Path(sessions)
        self.outputs = Path(outputs).absolute()
        self.workspace = Path(workspace).absolute()
        self.project_id = project_id
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'chat_artifacts.sqlite3'
        with self._connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS artifacts ('
                'id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL, '
                'path TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL, '
                'UNIQUE(session_id,turn_id,path))')

    def _connect(self):
        # SQLite's default journal already supplies atomic transactions. Changing
        # journal mode on every request can itself fail with SQLITE_BUSY while a
        # second process initializes the same private catalog.
        return sqlite3.connect(self.path, timeout=10)

    def _relative(self, value: str, inbox_scope: str | None, *, canonical: bool = False) -> Path:
        if (not isinstance(value, str) or not value.strip() or '\\' in value
                or any(ord(char) < 32 for char in value)
                or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', value)):
            raise ArtifactError('交付路径无效')
        original = Path(value)
        if '..' in original.parts:
            raise ArtifactError('交付路径不能穿越目录', 403)
        if canonical:
            if original.is_absolute():
                raise ArtifactError('交付记录中的路径无效', 403)
            relative = original
        elif original.is_absolute():
            try:
                relative = original.relative_to(self.outputs)
            except ValueError:
                try:
                    relative = original.relative_to(self.outputs.resolve())
                except ValueError:
                    raise ArtifactError('只能交付当前 IP 的 outputs 文件', 403) from None
        else:
            # Match upstream present: relative input belongs to the Session cwd.
            # Never guess outputs/report.txt when the agent named cwd/report.txt.
            try:
                relative = (self.workspace / original).relative_to(self.outputs)
            except ValueError:
                raise ArtifactError('只能交付当前 IP 的 outputs 文件；相对路径以会话工作目录为准', 403) from None
        if not relative.parts:
            raise ArtifactError('只能交付普通文件，不能交付目录', 403)
        for part in relative.parts:
            lower = part.lower()
            credential_text = (Path(lower).suffix in {'.js', '.json', '.yaml', '.yml', '.toml', '.ini', '.txt', '.cfg', '.conf'}
                               and Path(lower).stem in _CREDENTIAL_STEMS)
            if (part.startswith('.') or lower in _CREDENTIAL_NAMES or credential_text
                    or lower.endswith(('.pem', '.key', '.p12', '.pfx'))):
                raise ArtifactError('不能交付私有配置或凭据文件', 403)
        first = relative.parts[0]
        if first.startswith('_'):
            package = first == '_publish' and relative.suffix.lower() == '.zip'
            inbox = (first == '_inbox' and inbox_scope is not None
                     and len(relative.parts) >= 4 and relative.parts[1] == inbox_scope)
            if not package and not inbox:
                raise ArtifactError('该系统目录不能作为聊天交付物', 403)
        return relative

    def _checked_file(self, relative: Path) -> BinaryIO:
        """Open a regular file without following links, including parent links."""
        candidate = self.outputs / relative
        try:
            root_info = self.outputs.lstat()
            if (stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode)
                    or (hasattr(self.outputs, 'is_junction') and self.outputs.is_junction())):
                raise ArtifactError('交付目录不能是符号链接', 403)
            current = self.outputs
            for part in relative.parts:
                current = current / part
                info = current.lstat()
                if stat.S_ISLNK(info.st_mode) or (hasattr(current, 'is_junction') and current.is_junction()):
                    raise ArtifactError('不能交付符号链接文件或目录', 403)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.outputs.resolve())
            if not stat.S_ISREG(info.st_mode):
                raise ArtifactError('只能交付普通文件，不能交付目录', 403)
            if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
                # Do not resolve the root before opening: that would turn a
                # replaced outputs symlink into an apparently trusted directory.
                directory = os.open(self.outputs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    opened_root = os.fstat(directory)
                    if (opened_root.st_dev, opened_root.st_ino) != (root_info.st_dev, root_info.st_ino):
                        raise ArtifactError('交付目录已变化，请重新声明', 403)
                    for part in relative.parts[:-1]:
                        following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                        os.close(directory)
                        directory = following
                    descriptor = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                finally:
                    os.close(directory)
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    os.close(descriptor)
                    raise ArtifactError('只能交付普通文件', 403)
                return os.fdopen(descriptor, 'rb')
            # Portable fallback: verify the actual opened file identity and the
            # path again before exposing the handle to a response.
            stream = candidate.open('rb')
            try:
                after = candidate.stat(follow_symlinks=False)
                actual = os.fstat(stream.fileno())
                after_root = self.outputs.lstat()
                if (stat.S_ISLNK(after_root.st_mode)
                        or (after_root.st_dev, after_root.st_ino) != (root_info.st_dev, root_info.st_ino)
                        or candidate.is_symlink() or not stat.S_ISREG(actual.st_mode)
                        or (after.st_dev, after.st_ino) != (actual.st_dev, actual.st_ino)
                        or candidate.resolve() != resolved):
                    raise ArtifactError('交付文件路径已变化，请重新声明', 403)
            except BaseException:
                stream.close()
                raise
            return stream
        except ArtifactError:
            raise
        except (FileNotFoundError, NotADirectoryError):
            raise ArtifactError('交付文件不存在', 404) from None
        except (OSError, RuntimeError, ValueError):
            raise ArtifactError('交付文件不可读取或路径不安全', 403) from None

    def present(self, files: list[dict], *, session_id: str, turn_id: str,
                call_id: str, inbox_scope: str | None = None) -> list[dict]:
        if (not isinstance(files, list) or not 1 <= len(files) <= 8
                or not all(isinstance(value, str) and value for value in (session_id, turn_id, call_id))):
            raise ArtifactError('交付声明需要有效会话、轮次、调用和 1–8 个文件')
        # Validate ALL paths and metadata before the transaction. Dictionary
        # replacement keeps the first position but the final description.
        validated: dict[str, dict] = {}
        for item in files:
            if (not isinstance(item, dict) or set(item) - {'path', 'description'}
                    or not isinstance(item.get('path'), str)
                    or ('description' in item and not isinstance(item['description'], str))):
                raise ArtifactError('交付文件声明格式无效')
            relative = self._relative(item['path'], inbox_scope)
            with self._checked_file(relative) as stream:
                size = os.fstat(stream.fileno()).st_size
            path = relative.as_posix()
            identifier = hashlib.sha256('\0'.join((str(self.directory.resolve()), self.project_id,
                                                   session_id, turn_id, path)).encode('utf-8')).hexdigest()[:32]
            metadata = {
                'artifactId': identifier, 'projectId': self.project_id, 'sessionId': session_id,
                'turnId': turn_id, 'path': path, 'name': relative.name,
                'workspacePath': (Path('outputs') / relative).as_posix(),
                'mime': mimetypes.guess_type(relative.name)[0] or 'application/octet-stream',
                'size': size, 'status': 'available', 'createdAt': int(time.time() * 1000),
                'url': f'/api/chat/artifacts/{identifier}/content',
            }
            if 'description' in item:
                metadata['description'] = item['description']
            validated[path] = metadata
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            position = connection.execute('SELECT COALESCE(MAX(position),-1)+1 FROM artifacts '
                'WHERE session_id=? AND turn_id=?', (session_id, turn_id)).fetchone()[0]
            result = []
            for path, metadata in validated.items():
                old = connection.execute('SELECT position,payload FROM artifacts WHERE id=?', (metadata['artifactId'],)).fetchone()
                if old:
                    index = old[0]
                    metadata['createdAt'] = json.loads(old[1])['createdAt']
                else:
                    index, position = position, position + 1
                connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?) '
                    'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                    (metadata['artifactId'], session_id, turn_id, path, index, json.dumps(metadata, ensure_ascii=False)))
                result.append(metadata)
        return result

    def get(self, artifact_id: str) -> dict | None:
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[a-f0-9]{32}', artifact_id):
            return None
        with self._connect() as connection:
            row = connection.execute('SELECT payload FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def for_turn(self, session_id: str, turn_id: str) -> list[dict]:
        with self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute('SELECT payload FROM artifacts '
                'WHERE session_id=? AND turn_id=? ORDER BY position', (session_id, turn_id))]

    def open_content(self, artifact_id: str, *, inbox_scope: str | None = None) -> BinaryIO:
        metadata = self.get(artifact_id)
        if metadata is None:
            raise ArtifactError('交付物不存在', 404)
        relative = self._relative(metadata['path'], inbox_scope, canonical=True)
        return self._checked_file(relative)

    def read_related(self, artifact_id: str, path: str, *, inbox_scope: str | None = None) -> tuple[bytes, str]:
        metadata = self.get(artifact_id)
        if metadata is None:
            raise ArtifactError('交付物不存在', 404)
        return self.read_document_related(metadata['path'], path, inbox_scope=inbox_scope)

    def read_document_related(self, document: str, path: str, *, inbox_scope: str | None = None) -> tuple[bytes, str]:
        """Read a bounded HTML dependency without exposing a generic file reader.

        `path` is the HTML URL's already-decoded file path. Do not unquote it a
        second time or reinterpret query/fragment characters as URL syntax.
        """
        source = self._relative(document, inbox_scope, canonical=True)
        if source.suffix.lower() not in {'.html', '.htm'}:
            raise ArtifactError('相关资源必须属于可读取的 HTML 文件', 415)
        with self._checked_file(source):
            pass
        if (not isinstance(path, str) or not path or '\\' in path
                or any(ord(character) < 32 for character in path)
                or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', path)):
            raise ArtifactError('HTML 资源相对路径无效', 400)
        if path.startswith('/'):
            raise ArtifactError('HTML 资源不能使用绝对路径', 403)
        combined = posixpath.normpath(posixpath.join(source.parent.as_posix(), path))
        if combined == '..' or combined.startswith('../'):
            raise ArtifactError('HTML 资源不能离开当前 outputs 目录', 403)
        relative = self._relative(combined, inbox_scope, canonical=True)
        if source.parts[0] == '_inbox' and relative.parts[:2] != source.parts[:2]:
            raise ArtifactError('HTML 依赖不属于当前会话附件空间', 403)
        if relative.suffix.lower() not in _RELATED_EXTENSIONS:
            raise ArtifactError('HTML 预览只读取静态图片、CSS 和经典 JavaScript', 415)
        with self._checked_file(relative) as stream:
            if os.fstat(stream.fileno()).st_size > MAX_RELATED_BYTES:
                raise ArtifactError('HTML 单个资源不能超过 4 MiB', 413)
            # Read at most cap+1 even if another process grows the current file
            # after fstat. The verified handle closes before returning any bytes.
            content = stream.read(MAX_RELATED_BYTES + 1)
            if len(content) > MAX_RELATED_BYTES:
                raise ArtifactError('HTML 单个资源不能超过 4 MiB', 413)
        return content, mimetypes.guess_type(relative.name)[0] or 'application/octet-stream'
