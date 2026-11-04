"""Bind the existing MP Markdown converter's embedded images to fixed media.

This is a validation/resource-binding adapter, not a second Markdown renderer.
The Host supplies the existing pure converter. Original source paths are never
read here; execution materializes only the approved HTML and private stage files.
"""
from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import quote, unquote_to_bytes

from easel.artifact_blobs import ArtifactBlobStore, SnapshotError
from easel.chat_artifacts import ArtifactError


_ID = re.compile(r'[a-f0-9]{32}')
_SHA = re.compile(r'[a-f0-9]{64}')
_PLACEHOLDER = re.compile(r'easel-artifact:([a-f0-9]{32})')
_RASTER = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}
_TAGS = {'section', 'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote',
         'pre', 'code', 'ul', 'ol', 'li', 'strong', 'em', 'b', 'i', 'u', 's',
         'span', 'br', 'hr', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'a', 'img'}
_ATTRIBUTES = {'style', 'title', 'align', 'width', 'height', 'colspan', 'rowspan', 'id', 'class', 'alt'}
_CSS_DYNAMIC = re.compile(r'@|\\|(?:url|expression|image|image-set|-webkit-image-set|var|attr)\s*\('
                          r'|(?:behavior|-moz-binding)\s*:', re.I)


def _fail(message='公众号正文包含未批准或不支持的图片资源', status=400):
    raise ArtifactError(message, status)


def _images(media):
    if not isinstance(media, list) or not media:
        _fail('公众号文章需要已封存的封面图片')
    result = {}
    for entry in media:
        try:
            identifier, path = entry['artifactId'], entry['path']
            relative = PurePosixPath(path)
            if (not isinstance(identifier, str) or not _ID.fullmatch(identifier)
                    or not isinstance(path, str) or not path or relative.is_absolute()
                    or '\\' in path or any(ord(char) < 32 or ord(char) == 127 for char in path)
                    or any(part in ('.', '..') or part.startswith(('.', '_')) for part in relative.parts)
                    or re.match(r'^[a-z][a-z\d+.-]*:', path, re.I)
                    or entry.get('name') != relative.name or relative.suffix.lower() not in _RASTER
                    or not isinstance(entry['mime'], str) or not entry['mime'].startswith('image/')
                    or entry['mime'].lower() == 'image/svg+xml'
                    or type(entry['size']) is not int or entry['size'] < 0
                    or not isinstance(entry['sha256'], str) or not _SHA.fullmatch(entry['sha256'])
                    or type(entry['revision']) is not int or entry['revision'] < 1
                    or entry.get('bundleSha256') is not None
                    or entry.get('storageMode', 'snapshot') != 'snapshot'):
                _fail('公众号媒体必须是已封存的普通图片版本', 409)
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, ArtifactError):
                raise
            _fail('公众号媒体版本信息无效', 409)
        if identifier in result:
            _fail('同一图片版本不能重复选择', 409)
        result[identifier] = entry
    return result


class _BoundImages(HTMLParser):
    def __init__(self, document, resolve):
        super().__init__(convert_charrefs=True)
        self.document, self.resolve = document, resolve
        self.lines = [0] + [match.end() for match in re.finditer('\n', document)]
        self.patches = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        if tag not in _TAGS:
            _fail('公众号正文不支持动态嵌入、SVG 或外部样式资源')
        values = {}
        for name, value in attrs:
            if name in values or name.startswith('on') or ':' in name:
                _fail('公众号正文不允许重复属性或动态事件')
            if name not in _ATTRIBUTES and not (tag == 'img' and name == 'src') and not (tag == 'a' and name == 'href'):
                _fail('公众号正文包含未批准的资源属性')
            value = value or ''
            if name == 'style':
                clean = re.sub(r'/\*.*?\*/', '', value, flags=re.S)
                if _CSS_DYNAMIC.search(clean):
                    _fail('公众号正文不能通过 CSS 引用可变资源')
            if name == 'href' and not re.match(r'^(?:https?://|mailto:|tel:|#)', value.strip(), re.I):
                _fail('公众号链接需要使用公开网址或文内锚点')
            values[name] = value
        if tag != 'img':
            return
        if not values.get('src'):
            _fail('公众号图片缺少已选媒体引用')
        target, identifier = self.resolve(values['src'])
        values['src'] = target
        self.ids.append(identifier)
        row, column = self.getpos()
        start = self.lines[row - 1] + column
        raw = self.get_starttag_text()
        encoded = ' '.join(f'{name}="{html.escape(value, quote=True)}"' for name, value in values.items())
        self.patches.append((start, start + len(raw), f'<img {encoded}/>'))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag not in _TAGS:
            _fail('公众号正文不支持动态嵌入或 SVG')

    def handle_comment(self, data):
        # The legacy MP uploader finds img/src with a regex, including inside
        # comments. Never leave an unparsed image occurrence for it to reopen.
        _fail('公众号正文不能包含隐藏资源声明')

    def handle_decl(self, decl):
        _fail('公众号正文不支持文档或资源声明')

    def unknown_decl(self, data):
        _fail('公众号正文不支持隐藏资源声明')

    def handle_pi(self, data):
        _fail('公众号正文不支持处理指令')

    def bound(self):
        self.feed(self.document)
        self.close()
        starts = {start for start, _, _ in self.patches}
        for match in re.finditer(r'<\s*img\b', self.document, flags=re.I):
            if match.start() not in starts:
                _fail('公众号正文包含无法安全绑定的图片标记')
        result = self.document
        for start, end, replacement in reversed(self.patches):
            result = result[:start] + replacement + result[end:]
        return result, self.ids


def _bind(document, resolve):
    if (not isinstance(document, str) or any(ord(char) < 32 and char not in '\n\r\t' for char in document)):
        _fail('公众号排版结果无效')
    try:
        return _BoundImages(document, resolve).bound()
    except (UnicodeError, ValueError, TypeError) as error:
        if isinstance(error, ArtifactError):
            raise
        _fail('公众号排版结果无法安全解析')


def prepare_publication_article(title, body, media, outputs_root, converter):
    if not isinstance(title, str) or not isinstance(body, str) or not callable(converter):
        _fail('公众号文章需要有效标题、正文与本机排版器')
    images = _images(media)
    root = Path(outputs_root)
    if not root.is_absolute() or '..' in root.parts:
        _fail('公众号媒体根目录必须由服务器提供')
    aliases = {}
    for identifier, entry in images.items():
        path = entry['path']
        for alias in (path, 'outputs/' + path, (root / path).as_posix()):
            aliases.setdefault(alias, set()).add(identifier)
    def resolve(source):
        source = source.strip()
        placeholder = _PLACEHOLDER.fullmatch(source)
        if placeholder:
            identifier = placeholder[1]
            if identifier not in images:
                _fail('公众号图片不属于已选固定版本', 403)
            return source, identifier
        candidates = aliases.get(source)
        if candidates is None and '%' in source:
            try:
                if re.search(r'%(?![a-f\d]{2})', source, re.I):
                    raise ValueError
                decoded = unquote_to_bytes(source).decode('utf-8', errors='strict')
            except (ValueError, UnicodeError):
                _fail('公众号图片路径编码无效')
            candidates = aliases.get(decoded)
        if not candidates:
            _fail('公众号正文图片必须匹配已选固定媒体，不能使用私有、远端或未选路径', 403)
        if len(candidates) != 1:
            _fail('正文图片路径对应多个版本，请使用明确的固定版本', 409)
        identifier = next(iter(candidates))
        return 'easel-artifact:' + identifier, identifier
    markdown = f'# {title}\n\n{body}\n'
    bound, used = _bind(converter(markdown), resolve)
    extra = [identifier for identifier in list(images)[1:] if identifier not in used]
    if extra:
        markdown += '\n\n' + '\n\n'.join(f'![](easel-artifact:{identifier})' for identifier in extra)
        bound, _ = _bind(converter(markdown), resolve)
    try:
        digest = hashlib.sha256(bound.encode('utf-8')).hexdigest()
    except UnicodeError:
        _fail('公众号排版结果需要有效 UTF-8 内容')
    return {'html':bound, 'sha256':digest}


def _verified_stage_path(value, entry, index):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        _fail('公众号暂存图片路径无效', 409)
    path = Path(value)
    suffix = PurePosixPath(entry['path']).suffix.lower()
    if (not path.is_absolute() or '..' in path.parts or path.name != f"{index + 1:04d}-{entry['artifactId']}{suffix}"):
        _fail('公众号图片没有指向本次固定版本暂存文件', 409)
    try:
        if not path.parent.is_dir():
            _fail('公众号暂存图片不存在', 409)
        guard = ArtifactBlobStore(path.parent)
        with guard._root_fd() as directory:
            descriptor = guard._open_at(directory, path.name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size != entry['size']:
                    _fail('公众号暂存图片大小与批准版本不一致', 409)
                digest, size = hashlib.sha256(), 0
                while True:
                    chunk = stream.read(min(1024 * 1024, entry['size'] - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > entry['size']:
                        _fail('公众号暂存图片在验证期间发生变化', 409)
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
                if (size != entry['size'] or digest.hexdigest() != entry['sha256']
                        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                    _fail('公众号暂存图片摘要与批准版本不一致', 409)
            guard._check_root()
    except SnapshotError as error:
        raise ArtifactError(str(error), error.status_code) from None
    except OSError:
        _fail('公众号暂存图片无法安全读取', 409)
    # The existing MP resolver decodes exactly once. Encode literal percent and
    # other URL-sensitive filename characters so it receives the actual stage path.
    return quote(path.as_posix(), safe='/:')


def materialize_publication_article(article, media, staged_paths):
    images = _images(media)
    if (not isinstance(article, dict) or set(article) != {'html', 'sha256'}
            or not isinstance(article['html'], str) or not isinstance(article['sha256'], str)
            or not _SHA.fullmatch(article['sha256'])):
        _fail('公众号固定排版信息无效', 409)
    try:
        if hashlib.sha256(article['html'].encode('utf-8')).hexdigest() != article['sha256']:
            _fail('公众号固定排版摘要校验失败', 409)
    except UnicodeError:
        _fail('公众号固定排版内容损坏', 409)
    if not isinstance(staged_paths, list) or len(staged_paths) != len(images):
        _fail('公众号暂存图片与批准媒体数量不一致', 409)
    targets = {identifier:_verified_stage_path(staged_paths[index], entry, index)
               for index, (identifier, entry) in enumerate(images.items())}
    def resolve(source):
        match = _PLACEHOLDER.fullmatch(source)
        if match is None or match[1] not in targets:
            _fail('公众号固定排版包含未批准的图片来源', 409)
        return targets[match[1]], match[1]
    return _bind(article['html'], resolve)[0]
