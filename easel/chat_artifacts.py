"""D01 explicit deliverables, adapted from DeepSeek Harness tool-present (MIT).

Upstream: packages/deliverables/tool-present/src/index.ts and src/types.ts,
ce7073d51254c26cd63b22a60835b8f948deb447. Copyright (c) 2026 DeepSeek.
The original notice is retained in integrations/deepseek-harness/LICENSE.

Validate the complete files batch, then atomically record successful declarations.
Unlike the generic upstream Host, Easel accepts only the selected IP's approved
outputs. D08 snapshots new declarations; migrated D01 records explicitly retain
their legacy-current semantics without inventing historical bytes or approval.
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
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import BinaryIO
from easel.artifact_blobs import ArtifactBlobStore, SnapshotError


class ArtifactError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class ArtifactBatch(list):
    """A canonical declaration receipt; replays must not publish another event."""
    def __init__(self, values, *, replayed=False):
        super().__init__(values)
        self.replayed = replayed


_CREDENTIAL_NAMES = {
    'auth.json', 'cookies.json', 'cookies.txt', 'credentials', 'credentials.json',
    'credentials.yaml', 'credentials.yml', 'credentials.toml', 'secrets.json',
    'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519',
}
_CREDENTIAL_STEMS = {'credentials', 'secrets', 'secret', 'cookies', 'api_key', 'api-key',
                     'access_token', 'access-token', 'refresh_token', 'refresh-token', 'password', 'passwords'}


def is_private_output_part(part: str) -> bool:
    lower = part.lower()
    name = Path(lower)
    credential_text = (name.suffix in {'.js', '.json', '.yaml', '.yml', '.toml', '.ini', '.txt', '.cfg', '.conf'}
                       and name.stem in _CREDENTIAL_STEMS)
    return (part.startswith('.') or lower in _CREDENTIAL_NAMES or credential_text
            or lower.endswith(('.pem', '.key', '.p12', '.pfx')))


_RELATED_EXTENSIONS = {'.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.ico', '.svg'}
# Same per-asset limit as upstream ui-sidebar-documentpreview/src/client/html/pack.ts.
MAX_RELATED_BYTES = 4 * 1024 * 1024
MAX_HTML_BYTES = 32 * 1024 * 1024
MAX_HTML_ASSETS = 64


class _HtmlReferences(HTMLParser):
    """Server port of vendor/html/pack.ts's finite, non-executing selector."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.seen = set()
        self.base = False
        self.overflow = False
        self.template_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'template':
            self.template_depth += 1
        if self.template_depth:
            return
        values = {}
        for key, value in attrs:
            values.setdefault(key, value or '')
        if tag == 'base' and 'href' in values:
            self.base = True
        if tag not in {'script', 'link', 'img'}:
            return
        if tag == 'script' and values.get('type', '').strip().lower() not in {'', 'text/javascript', 'application/javascript'}:
            return
        if tag == 'link' and 'stylesheet' not in values.get('rel', '').lower().split():
            return
        reference = values.get('href' if tag == 'link' else 'src', '')
        if not reference or re.match(r'^(?:[a-z][a-z\d+.-]*:|[/\\#?])', reference, re.I) or '\0' in reference:
            return
        path = re.split(r'[?#]', reference, 1)[0]
        suffix = posixpath.splitext(path)[1].lower()
        allowed = {'.js'} if tag == 'script' else {'.css'} if tag == 'link' else _RELATED_EXTENSIONS - {'.css', '.js'}
        if suffix not in allowed or (tag, reference) in self.seen:
            return
        if len(self.references) >= MAX_HTML_ASSETS:
            self.overflow = True
            return
        self.seen.add((tag, reference))
        self.references.append(path)

    def handle_endtag(self, tag):
        if tag == 'template' and self.template_depth:
            self.template_depth -= 1


class ChatArtifactStore:
    def __init__(self, sessions: Path, outputs: Path, workspace: Path, project_id: str):
        self.directory = Path(sessions)
        self.outputs = Path(outputs).absolute()
        self.workspace = Path(workspace).absolute()
        self.project_id = project_id
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'chat_artifacts.sqlite3'
        with self._connect() as connection:
            if self._schema_ready(connection):
                return
            connection.execute('BEGIN IMMEDIATE')
            if self._schema_ready(connection):
                return
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone()
            rebuild = False
            if exists:
                for index in connection.execute('PRAGMA index_list(artifacts)').fetchall():
                    name = index[1].replace('"', '""')
                    columns = [item[2] for item in connection.execute(f'PRAGMA index_info("{name}")')]
                    if index[2] and columns == ['session_id', 'turn_id', 'path']:
                        rebuild = True
                if rebuild:
                    connection.execute('ALTER TABLE artifacts RENAME TO artifacts_v1_migration')
            connection.execute('CREATE TABLE IF NOT EXISTS artifacts ('
                'id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL, '
                'path TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL)')
            if rebuild:
                connection.execute('INSERT INTO artifacts SELECT id,session_id,turn_id,path,position,payload FROM artifacts_v1_migration')
                connection.execute('DROP TABLE artifacts_v1_migration')
            connection.execute('CREATE INDEX IF NOT EXISTS artifact_lineage ON artifacts(session_id,path)')
            connection.execute('CREATE TABLE IF NOT EXISTS artifact_declarations ('
                'session_id TEXT NOT NULL,turn_id TEXT NOT NULL,call_id TEXT NOT NULL,args_hash TEXT NOT NULL,'
                'receipt TEXT NOT NULL,PRIMARY KEY(session_id,turn_id,call_id))')
            connection.execute('CREATE TABLE IF NOT EXISTS artifact_resources ('
                'artifact_id TEXT NOT NULL,path TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(artifact_id,path))')
            if connection.execute('PRAGMA user_version').fetchone()[0] < 2 or rebuild:
                for identifier, raw in connection.execute('SELECT id,payload FROM artifacts').fetchall():
                    value = self._metadata(json.loads(raw))
                    connection.execute('UPDATE artifacts SET payload=? WHERE id=?', (json.dumps(value, ensure_ascii=False), identifier))
                connection.execute('PRAGMA user_version=2')

    @staticmethod
    def _schema_ready(connection):
        if connection.execute('PRAGMA user_version').fetchone()[0] < 2:
            return False
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('artifacts','artifact_declarations','artifact_resources')")}
        if names != {'artifacts', 'artifact_declarations', 'artifact_resources'}:
            return False
        for index in connection.execute('PRAGMA index_list(artifacts)'):
            name = index[1].replace('"', '""')
            if index[2] and [item[2] for item in connection.execute(f'PRAGMA index_info("{name}")')] == ['session_id', 'turn_id', 'path']:
                return False
        return True

    def _connect(self):
        # SQLite's default journal already supplies atomic transactions. Changing
        # journal mode on every request can itself fail with SQLITE_BUSY while a
        # second process initializes the same private catalog.
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def _metadata(value):
        if not isinstance(value, dict):
            raise ArtifactError('交付版本记录损坏', 409)
        mode = value.get('storageMode')
        if mode not in (None, 'legacy-current', 'snapshot'):
            raise ArtifactError('不支持的交付存储模式，未回退当前文件', 409)
        if mode != 'snapshot':
            value.update(storageMode='legacy-current', revision=None, sha256=None, bundleSha256=None, reviewStatus='unknown')
        elif (type(value.get('revision')) is not int or value['revision'] < 1
              or not isinstance(value.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', value['sha256'])
              or type(value.get('size')) is not int or value['size'] < 0):
            raise ArtifactError('交付快照记录损坏，未回退当前文件', 409)
        return value

    def _blobs(self):
        return ArtifactBlobStore(self.directory / 'artifact_blobs')

    @staticmethod
    def _check_cancelled(cancelled):
        if cancelled is not None and cancelled():
            raise ArtifactError('交付快照已取消，未提交新版本', 499)

    @staticmethod
    def _receipt(connection, session_id, turn_id, call_id, args_hash):
        row = connection.execute('SELECT args_hash,receipt FROM artifact_declarations WHERE session_id=? AND turn_id=? AND call_id=?',
                                 (session_id, turn_id, call_id)).fetchone()
        if row is None:
            return None
        if row[0] != args_hash:
            raise ArtifactError('同一交付调用不能绑定不同参数', 409)
        return ArtifactBatch(json.loads(row[1]), replayed=True)

    def _capture(self, relative, *, inbox_scope, cancelled):
        self._check_cancelled(cancelled)
        html = relative.suffix.lower() in {'.html', '.htm'}
        with self._checked_file(relative) as stream:
            blob = self._blobs().capture(stream, max_bytes=MAX_HTML_BYTES if html else None, cancelled=cancelled)
        self._check_cancelled(cancelled)
        resources = {}
        if html:
            with self._blobs().open_blob(blob['sha256'], blob['size']) as stream:
                data = stream.read(MAX_HTML_BYTES + 1)
            try:
                parser = _HtmlReferences()
                parser.feed(data.decode('utf-8-sig', errors='strict'))
                parser.close()
            except (UnicodeError, ValueError):
                raise ArtifactError('HTML 快照需要 UTF-8 文件', 415) from None
            if not parser.base:
                if parser.overflow:
                    raise ArtifactError('HTML 快照直接资源不能超过 64 项', 413)
                total = blob['size']
                for encoded in parser.references:
                    self._check_cancelled(cancelled)
                    try:
                        if re.search(r'%(?![a-fA-F0-9]{2})', encoded):
                            raise ValueError
                        path = urllib.parse.unquote_to_bytes(encoded).decode('utf-8', errors='strict')
                    except (UnicodeError, ValueError):
                        raise ArtifactError('HTML 资源 URL 编码无效', 400) from None
                    target = self._related_path(relative, path, inbox_scope)
                    key = target.as_posix()
                    if key not in resources:
                        payload, mime = self.read_document_related(relative.as_posix(), path, inbox_scope=inbox_scope)
                        captured = self._blobs().capture_bytes(payload, max_bytes=MAX_RELATED_BYTES, cancelled=cancelled)
                        resources[key] = {**captured, 'mime': mime}
                    total += resources[key]['size']
                    if total > MAX_HTML_BYTES:
                        raise ArtifactError('HTML 快照总大小不能超过 32 MiB', 413)
            bundle = hashlib.sha256(json.dumps({'root': blob, 'resources': resources}, ensure_ascii=False,
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        else:
            bundle = None
        self._check_cancelled(cancelled)
        return {**blob, 'bundleSha256': bundle, 'resources': resources}

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
        if any(is_private_output_part(part) for part in relative.parts):
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
                call_id: str, inbox_scope: str | None = None, cancelled=None) -> list[dict]:
        if (not isinstance(files, list) or not 1 <= len(files) <= 8
                or not all(isinstance(value, str) and value for value in (session_id, turn_id, call_id))):
            raise ArtifactError('交付声明需要有效会话、轮次、调用和 1–8 个文件')
        for item in files:
            if (not isinstance(item, dict) or set(item) - {'path', 'description'}
                    or not isinstance(item.get('path'), str)
                    or ('description' in item and not isinstance(item['description'], str))):
                raise ArtifactError('交付文件声明格式无效')
        self._check_cancelled(cancelled)
        args_hash = hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True,
                                              separators=(',', ':')).encode('utf-8')).hexdigest()
        with self._connect() as connection:
            receipt = self._receipt(connection, session_id, turn_id, call_id, args_hash)
            if receipt is not None:
                return receipt
        validated = {}
        try:
            for item in files:
                relative = self._relative(item['path'], inbox_scope)
                captured = self._capture(relative, inbox_scope=inbox_scope, cancelled=cancelled)
                path = relative.as_posix()
                metadata = {
                    'projectId': self.project_id, 'sessionId': session_id, 'turnId': turn_id,
                    'path': path, 'name': relative.name, 'workspacePath': (Path('outputs') / relative).as_posix(),
                    'mime': mimetypes.guess_type(relative.name)[0] or 'application/octet-stream',
                    'size': captured['size'], 'sha256': captured['sha256'], 'bundleSha256': captured['bundleSha256'],
                    'storageMode': 'snapshot', 'reviewStatus': 'unreviewed', 'status': 'available',
                }
                if 'description' in item:
                    metadata['description'] = item['description']
                validated[path] = (metadata, captured['resources'])
        except SnapshotError as error:
            raise ArtifactError(str(error), error.status_code) from None
        self._check_cancelled(cancelled)
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            receipt = self._receipt(connection, session_id, turn_id, call_id, args_hash)
            if receipt is not None:
                return receipt
            position = connection.execute('SELECT COALESCE(MAX(position),-1)+1 FROM artifacts '
                'WHERE session_id=? AND turn_id=?', (session_id, turn_id)).fetchone()[0]
            result = []
            for path, (metadata, resources) in validated.items():
                self._check_cancelled(cancelled)
                lineage = [self._metadata(json.loads(row[0])) for row in connection.execute(
                    'SELECT payload FROM artifacts WHERE session_id=? AND path=?', (session_id, path))]
                snapshots = [item for item in lineage if item.get('storageMode') == 'snapshot']
                latest = max(snapshots, key=lambda item: item['revision'], default=None)
                reuse = (latest is not None and latest['turnId'] == turn_id and latest['sha256'] == metadata['sha256']
                         and latest.get('bundleSha256') == metadata.get('bundleSha256'))
                if reuse:
                    identifier = latest['artifactId']
                    metadata.update(artifactId=identifier, revision=latest['revision'], createdAt=latest['createdAt'])
                    index = connection.execute('SELECT position FROM artifacts WHERE id=?', (identifier,)).fetchone()[0]
                else:
                    revision = (latest['revision'] if latest else 0) + 1
                    identifier = hashlib.sha256('\0'.join((str(self.directory.resolve()), self.project_id, session_id,
                        turn_id, path, 'snapshot', str(revision), metadata['sha256'], metadata.get('bundleSha256') or '')).encode()).hexdigest()[:32]
                    metadata.update(artifactId=identifier, revision=revision, createdAt=int(time.time() * 1000))
                    first = connection.execute('SELECT MIN(position) FROM artifacts WHERE session_id=? AND turn_id=? AND path=?',
                                               (session_id, turn_id, path)).fetchone()[0]
                    if first is None:
                        index, position = position, position + 1
                    else:
                        index = first
                metadata['url'] = f'/api/chat/artifacts/{identifier}/content'
                connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                    (identifier, session_id, turn_id, path, index, json.dumps(metadata, ensure_ascii=False)))
                if not reuse:
                    for resource_path, value in resources.items():
                        connection.execute('INSERT INTO artifact_resources VALUES(?,?,?)',
                                           (identifier, resource_path, json.dumps(value, ensure_ascii=False)))
                result.append(metadata)
            self._check_cancelled(cancelled)
            connection.execute('INSERT INTO artifact_declarations VALUES(?,?,?,?,?)',
                               (session_id, turn_id, call_id, args_hash, json.dumps(result, ensure_ascii=False)))
            self._check_cancelled(cancelled)
        return ArtifactBatch(result)

    def get(self, artifact_id: str) -> dict | None:
        if not isinstance(artifact_id, str) or not re.fullmatch(r'[a-f0-9]{32}', artifact_id):
            return None
        with self._connect() as connection:
            row = connection.execute('SELECT payload FROM artifacts WHERE id=?', (artifact_id,)).fetchone()
            return self._metadata(json.loads(row[0])) if row else None

    def list_metadata(self, *, after_id: str = '', limit: int = 20) -> list[dict]:
        """Read a private catalog page; older deliveries are never pruned.

        IDs provide a deterministic keyset independent of mutable descriptions.
        The caller owns authorization and the actor/IP-specific catalog root.
        Availability remains a separate verified-blob observation.
        """
        if (not isinstance(after_id, str) or after_id and not re.fullmatch(r'[a-f0-9]{32}', after_id)
                or type(limit) is not int or limit < 1):
            raise ArtifactError('交付目录分页参数无效')
        with self._connect() as connection:
            values = []
            for identifier, raw in connection.execute(
                    'SELECT id,payload FROM artifacts WHERE id>? ORDER BY id LIMIT ?', (after_id, limit)):
                try:
                    value = self._metadata(json.loads(raw))
                except (ValueError, TypeError):
                    raise ArtifactError('交付目录记录损坏，未回退当前文件', 409) from None
                if value.get('artifactId') != identifier:
                    raise ArtifactError('交付目录编号与版本记录不一致', 409)
                values.append(value)
            return values

    def for_turn(self, session_id: str, turn_id: str) -> list[dict]:
        with self._connect() as connection:
            return [self._metadata(json.loads(row[0])) for row in connection.execute('SELECT payload FROM artifacts '
                'WHERE session_id=? AND turn_id=? ORDER BY position,rowid', (session_id, turn_id))]

    def open_content(self, artifact_id: str, *, inbox_scope: str | None = None) -> BinaryIO:
        metadata = self.get(artifact_id)
        if metadata is None:
            raise ArtifactError('交付物不存在', 404)
        if metadata['storageMode'] == 'snapshot':
            try:
                return self._blobs().open_blob(metadata['sha256'], metadata['size'])
            except SnapshotError as error:
                raise ArtifactError(str(error), error.status_code) from None
        relative = self._relative(metadata['path'], inbox_scope, canonical=True)
        return self._checked_file(relative)

    def versions(self, artifact_id: str) -> list[dict]:
        metadata = self.get(artifact_id)
        if metadata is None:
            raise ArtifactError('交付物不存在', 404)
        with self._connect() as connection:
            values = [self._metadata(json.loads(row[0])) for row in connection.execute(
                'SELECT payload FROM artifacts WHERE session_id=? AND path=?', (metadata['sessionId'], metadata['path']))]
        values.sort(key=lambda value: (value.get('revision') or 0, value['createdAt']), reverse=True)
        return [{**value, 'latestDelivered': index == 0} for index, value in enumerate(values)]

    def verify_available(self, artifact_id: str, *, inbox_scope: str | None = None) -> None:
        metadata = self.get(artifact_id)
        if metadata is None:
            raise ArtifactError('交付物不存在', 404)
        with self.open_content(artifact_id, inbox_scope=inbox_scope):
            pass
        if metadata['storageMode'] == 'snapshot':
            values = self._manifest(metadata).values()
            try:
                for value in values:
                    with self._blobs().open_blob(value['sha256'], value['size']):
                        pass
            except SnapshotError as error:
                raise ArtifactError(str(error), error.status_code) from None

    def _manifest(self, metadata):
        try:
            with self._connect() as connection:
                resources = {row[0]: json.loads(row[1]) for row in connection.execute(
                    'SELECT path,payload FROM artifact_resources WHERE artifact_id=?', (metadata['artifactId'],))}
            if Path(metadata['path']).suffix.lower() in {'.html', '.htm'}:
                expected = hashlib.sha256(json.dumps({'root': {'sha256': metadata['sha256'], 'size': metadata['size']},
                    'resources': resources}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                if metadata.get('bundleSha256') != expected:
                    raise ArtifactError('HTML 版本资源清单校验失败', 409)
            elif resources or metadata.get('bundleSha256') is not None:
                raise ArtifactError('交付版本资源清单无效', 409)
            return resources
        except (ValueError, TypeError, KeyError) as error:
            if isinstance(error, ArtifactError):
                raise
            raise ArtifactError('交付版本资源清单损坏', 409) from None

    def read_related(self, artifact_id: str, path: str, *, inbox_scope: str | None = None) -> tuple[bytes, str]:
        metadata = self.get(artifact_id)
        if metadata is None:
            raise ArtifactError('交付物不存在', 404)
        if metadata['storageMode'] == 'snapshot':
            # Verify the root version as well; a corrupt package must never fall
            # through to a current filesystem read during preview.
            with self.open_content(artifact_id):
                pass
            try:
                source = self._relative(metadata['path'], inbox_scope, canonical=True)
                target = self._related_path(source, path, inbox_scope)
            except ArtifactError:
                raise ArtifactError('该版本未封存此 HTML 资源', 404) from None
            value = self._manifest(metadata).get(target.as_posix())
            if value is None:
                raise ArtifactError('该版本未封存此 HTML 资源', 404)
            try:
                with self._blobs().open_blob(value['sha256'], value['size']) as stream:
                    payload = stream.read(MAX_RELATED_BYTES + 1)
            except SnapshotError as error:
                raise ArtifactError(str(error), error.status_code) from None
            if len(payload) > MAX_RELATED_BYTES:
                raise ArtifactError('HTML 单个资源不能超过 4 MiB', 413)
            return payload, value['mime']
        return self.read_document_related(metadata['path'], path, inbox_scope=inbox_scope)

    def _related_path(self, source: Path, path: str, inbox_scope: str | None) -> Path:
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
        return relative

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
        relative = self._related_path(source, path, inbox_scope)
        with self._checked_file(relative) as stream:
            if os.fstat(stream.fileno()).st_size > MAX_RELATED_BYTES:
                raise ArtifactError('HTML 单个资源不能超过 4 MiB', 413)
            # Read at most cap+1 even if another process grows the current file
            # after fstat. The verified handle closes before returning any bytes.
            content = stream.read(MAX_RELATED_BYTES + 1)
            if len(content) > MAX_RELATED_BYTES:
                raise ArtifactError('HTML 单个资源不能超过 4 MiB', 413)
        return content, mimetypes.guess_type(relative.name)[0] or 'application/octet-stream'
