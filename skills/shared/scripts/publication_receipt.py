#!/usr/bin/env python3
"""Private, correlated publisher observations, independent of stdout and logs.

The host supplies both paths. This writer records evidence; only the host may
interpret it as a draft, submission, or independently verified publication.
Legacy CLI calls without the two optional flags retain their existing behavior.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid


_PHASES = ('not_started', 'submit_attempted', 'platform_ack', 'readback')
_REASONS = frozenset(('dry_run', 'help', 'preflight_failed', 'ui_signal_only',
    'login_required', 'account_mismatch', 'submit_error', 'platform_rejected',
    'readback_error', 'unverified', 'no_ack', 'cancelled', 'internal_error',
    'validation_failed', 'runner_unavailable'))
_CONTEXT_KEYS = ('operationId', 'payloadHash', 'platform', 'mode', 'approvedAccountId')
_PLATFORMS = frozenset(('douyin', 'xiaohongshu', 'wechat-oa', 'weixin-channels',
                       'bilibili', 'kuaishou', 'zhihu'))
_MODES = frozenset(('publish_now', 'create_draft', 'scheduled', 'manual_export'))
_ACK_STATES = frozenset(('accepted', 'submitted', 'processing', 'reviewing', 'published',
                        'public', 'private', 'unlisted', 'scheduled', 'draft', 'unknown', 'rejected'))
_ACCOUNT_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}:[A-Za-z0-9_.@+=-]{1,256}')
_CONTENT_ID = re.compile(r'[A-Za-z0-9_.+=-]{1,256}')
_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,79}')
_SAFE_DIR_FD = (hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY')
    and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd and os.rename in os.supports_dir_fd)
_LIMIT = 256 * 1024


class PublicationReceiptError(ValueError):
    """Safe fixed diagnostics: never include filesystem or provider details."""
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _fail(status_code=400):
    raise PublicationReceiptError('Publication receipt evidence or private storage is invalid', status_code) from None


def _identity(info):
    return info.st_dev, info.st_ino


def _facts(info):
    return (*_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _linked(info):
    return (stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))


def _path(value):
    raw = os.fspath(value)
    if (not isinstance(raw, str) or not os.path.isabs(raw)
            or any(ord(char) < 32 for char in raw)
            or any(part in ('.', '..') for part in raw.replace('\\', '/').split('/'))):
        _fail()
    path = Path(raw)
    if not path.name:
        _fail()
    return path


class _Directory:
    """Reopen the host directory without following links, pinning its identity.

    POSIX uses directory-relative operations. The portable path checks every
    component and file identity, without claiming native Windows reparse fencing.
    No directories are created or permissions of existing host state changed.
    """
    def __init__(self, path):
        self.path = path
        self.identity = None
        with self.open() as directory:
            info = os.fstat(directory) if isinstance(directory, int) else path.lstat()
            self.identity = _identity(info)

    def _portable_check(self):
        current = Path(self.path.anchor)
        info = current.lstat()
        for part in self.path.parts[1:]:
            current = current / part
            info = current.lstat()
            if _linked(info) or not stat.S_ISDIR(info.st_mode):
                _fail()
        if self.identity is not None and _identity(info) != self.identity:
            _fail()

    @contextmanager
    def open(self):
        descriptor = None
        try:
            if not _SAFE_DIR_FD:
                self._portable_check()
                yield self.path
                self._portable_check()
                return
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
            descriptor = os.open(self.path.anchor, flags)
            for part in self.path.parts[1:]:
                following = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = following
            if self.identity is not None and _identity(os.fstat(descriptor)) != self.identity:
                _fail()
            yield descriptor
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def check(self):
        with self.open():
            pass


def _stat(directory, name):
    try:
        return (os.stat(name, dir_fd=directory, follow_symlinks=False) if isinstance(directory, int)
                else (directory / name).lstat())
    except FileNotFoundError:
        return None


def _open(directory, name, flags, mode=0o600):
    flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    if isinstance(directory, int):
        return os.open(name, flags, mode, dir_fd=directory)
    info = _stat(directory, name)
    if info is not None and (_linked(info) or not stat.S_ISREG(info.st_mode)):
        _fail()
    descriptor = os.open(directory / name, flags, mode)
    try:
        after = _stat(directory, name)
        if (after is None or _linked(after) or not stat.S_ISREG(os.fstat(descriptor).st_mode)
                or _identity(after) != _identity(os.fstat(descriptor))):
            _fail()
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read(directory, name, *, optional=False):
    info = _stat(directory, name)
    if info is None:
        if optional:
            return None
        _fail()
    if (_linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > _LIMIT):
        _fail()
    descriptor = _open(directory, name, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or _facts(before) != _facts(info):
            _fail()
        data = stream.read(_LIMIT + 1)
        after = os.fstat(stream.fileno())
        entry = _stat(directory, name)
        if (len(data) > _LIMIT or _facts(before) != _facts(after)
                or entry is None or _linked(entry) or _facts(entry) != _facts(after)):
            _fail()
    return data, _facts(after)


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail()
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        _fail()
    return value


def _context(value, platform=None):
    if (not isinstance(value, dict) or set(value) != set(_CONTEXT_KEYS)
            or (platform is not None and value.get('platform') != platform)
            or value.get('platform') not in _PLATFORMS or value.get('mode') not in _MODES
            or not isinstance(value.get('operationId'), str)
            or not re.fullmatch(r'[a-f0-9]{32}', value['operationId'])
            or not isinstance(value.get('payloadHash'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', value['payloadHash'])
            or not isinstance(value.get('approvedAccountId'), str)
            or not _ACCOUNT_ID.fullmatch(value['approvedAccountId'])):
        _fail()
    return {key: value[key] for key in _CONTEXT_KEYS}


def _check_current(directory, name, expected):
    if _read(directory, name, optional=True) != expected:
        _fail(409)


def _atomic_write(root, name, value, expected, *, check=None, create_only=False):
    """Publish one private complete JSON file through the same pinned directory.

    Context is published without replacing an existing entry. Receipt revisions
    use atomic replace after verifying the last revision owned by this writer.
    """
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')
    if len(data) > _LIMIT:
        _fail()
    temporary = f'.publication-receipt-{uuid.uuid4().hex}.tmp'
    owned = None
    with root.open() as directory:
        try:
            if check is not None:
                check()
            _check_current(directory, name, expected)
            descriptor = _open(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(descriptor, 'wb') as stream:
                owned = _identity(os.fstat(stream.fileno()))
                if hasattr(os, 'fchmod'):
                    os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if check is not None:
                check()
            _check_current(directory, name, expected)
            root.check()
            info = _stat(directory, temporary)
            if info is None or _linked(info) or _identity(info) != owned:
                _fail()
            if isinstance(directory, int):
                if create_only:
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                    os.unlink(temporary, dir_fd=directory)
                else:
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            else:
                if create_only:
                    os.link(directory / temporary, directory / name, follow_symlinks=False)
                    (directory / temporary).unlink()
                else:
                    os.replace(directory / temporary, directory / name)
            root.check()
            if check is not None:
                check()
            current = _read(directory, name)
            if current[0] != data or current[1][:2] != owned:
                _fail()
            return current
        finally:
            if not isinstance(directory, int):
                # A Path cannot authorize cleanup in a replacement directory.
                root.check()
            info = _stat(directory, temporary)
            if info is not None and owned is not None and _identity(info) == owned:
                if isinstance(directory, int):
                    os.unlink(temporary, dir_fd=directory)
                else:
                    (directory / temporary).unlink()


class PublicationReceiptFiles:
    """Host capability for one operation's immutable context and runner receipt.

    The host creates the private operation directory before construction. A
    reopened identical context is read-only, enabling reconciliation; an existing
    different context is never rebound or overwritten. Receipt revisions remain
    untrusted evidence for the host normalizer, even when this file check passes.
    """
    def __init__(self, directory, context):
        try:
            expected = _context(context)
            root = _path(directory)
            self._root = _Directory(root)
            self.context_path = root / 'receipt-context.json'
            self.receipt_path = root / 'receipt.json'
            with self._root.open() as target:
                prior = _read(target, self.context_path.name, optional=True)
            if prior is None:
                try:
                    prior = _atomic_write(self._root, self.context_path.name, expected,
                                          None, create_only=True)
                except FileExistsError:
                    # Another constructor may have won the exclusive publish.
                    with self._root.open() as target:
                        prior = _read(target, self.context_path.name)
            if _context(_json(prior[0])) != expected:
                _fail(409)
            self._context_bytes, self._context_facts = prior
            self._root.check()
        except PublicationReceiptError:
            raise
        except Exception:
            _fail()

    def _check_context(self, directory):
        current = _read(directory, self.context_path.name)
        if current != (self._context_bytes, self._context_facts):
            _fail(409)

    def read(self):
        """Read bounded, unambiguous JSON without adopting a replaced context."""
        try:
            with self._root.open() as directory:
                self._check_context(directory)
                current = _read(directory, self.receipt_path.name, optional=True)
                value = _json(current[0]) if current is not None else None
                self._check_context(directory)
                self._root.check()
                return value
        except PublicationReceiptError:
            raise
        except Exception:
            _fail()


def _account(value, *, strict=True):
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get('authenticated') is not True
            or not isinstance(value.get('id'), str) or not _ACCOUNT_ID.fullmatch(value['id'])):
        if strict:
            _fail()
        return None
    return {'id': value['id'], 'authenticated': True}


def _ack(value):
    if (not isinstance(value, dict) or value.get('kind') not in ('draft', 'post')
            or value.get('accepted') is not True or value.get('source') != 'provider_response'
            or not isinstance(value.get('id'), str) or not _CONTENT_ID.fullmatch(value['id'])):
        _fail()
    result = {key: value[key] for key in ('kind', 'accepted', 'id', 'source')}
    if value.get('state') in _ACK_STATES:
        result['state'] = value['state']
    return result


def _text(value, limit):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(char) < 32 for char in value)
            or re.search(r'(?:[A-Za-z][A-Za-z0-9+.-]*:|www\.)', value)):
        return None
    return value


def _work(value):
    if not isinstance(value, dict):
        return None
    result = {}
    identifier = value.get('platform_content_id')
    if isinstance(identifier, str) and _CONTENT_ID.fullmatch(identifier):
        result['platform_content_id'] = identifier
    for key, limit in (('title', 1000), ('status', 100)):
        cleaned = _text(value.get(key), limit)
        if cleaned is not None:
            result[key] = cleaned
    when = value.get('published_at_ms')
    if when is None or (type(when) is int and 0 <= when <= 2**53 - 1):
        result['published_at_ms'] = when
    return result


def _readback(value):
    if not isinstance(value, dict):
        converter = getattr(value, 'as_dict', None)
        if not callable(converter):
            _fail()
        value = converter()
    if (not isinstance(value, dict) or value.get('outcome') not in
            ('verified', 'unverified', 'login_required', 'readback_error')):
        _fail()
    result = {'outcome': value['outcome'], 'matched': _work(value.get('matched')),
              'candidates': [], 'evidence': {}}
    if isinstance(value.get('candidates'), list):
        result['candidates'] = [clean for item in value['candidates'][:50]
                                if (clean := _work(item)) is not None]
    evidence = value.get('evidence')
    if isinstance(evidence, dict):
        for key in ('method', 'matchMethod', 'statusSource', 'visibility'):
            item = evidence.get(key)
            if isinstance(item, str) and _TOKEN.fullmatch(item):
                result['evidence'][key] = item
        for key in ('unique', 'baselineCaptured'):
            if type(evidence.get(key)) is bool:
                result['evidence'][key] = evidence[key]
        for key in ('sinceMs', 'checkedAtMs'):
            item = evidence.get(key)
            if type(item) is int and 0 <= item <= 2**53 - 1:
                result['evidence'][key] = item
        identity = _account(evidence.get('account'), strict=False)
        if identity is not None:
            result['evidence']['account'] = identity
    return result


class _NullReporter:
    enabled = False

    def record(self, phase, *, account=None, ack=None, readback=None, reason=None):
        return None


class PublicationReceiptReporter:
    enabled = True

    @classmethod
    def from_args(cls, args, platform):
        source = getattr(args, 'receipt_context', None)
        output = getattr(args, 'receipt_file', None)
        if not source and not output:
            return _NullReporter()
        if not source or not output:
            _fail()
        try:
            return cls(source, output, platform)
        except PublicationReceiptError:
            raise
        except (OSError, ValueError, TypeError, OverflowError, RecursionError):
            _fail()

    def __init__(self, source, output, platform):
        self._source, self._output = _path(source), _path(output)
        if self._source == self._output:
            _fail()
        self._source_root = _Directory(self._source.parent)
        self._output_root = _Directory(self._output.parent)
        with self._source_root.open() as directory:
            self._context_bytes, self._context_facts = _read(directory, self._source.name)
        context = _context(_json(self._context_bytes), platform)
        self._base = {'kind': 'publication_receipt', 'schemaVersion': 1, **context}
        self._value = dict(self._base)
        self._receipt_bytes = self._receipt_facts = None
        self._lock = threading.Lock()
        with self._output_root.open() as directory:
            prior = _read(directory, self._output.name, optional=True)
        if prior is not None:
            self._receipt_bytes, self._receipt_facts = prior
            value = _json(self._receipt_bytes)
            if (any(type(value.get(key)) is not type(item) or value.get(key) != item
                    for key, item in self._base.items()) or value.get('phase') not in _PHASES):
                _fail()
            # Reopening preserves existing ACK, but never imports unfiltered data.
            self._value['phase'] = value['phase']
            if value.get('account') is not None:
                self._value['account'] = _account(value['account'])
            if value.get('ack') is not None:
                self._value['ack'] = _ack(value['ack'])
            if value.get('readback') is not None:
                self._value['readback'] = _readback(value['readback'])
            if value.get('reason') in _REASONS:
                self._value['reason'] = value['reason']

    def _check_context(self):
        with self._source_root.open() as directory:
            current = _read(directory, self._source.name)
        if current != (self._context_bytes, self._context_facts):
            _fail()

    def _write(self, value):
        expected = (self._receipt_bytes, self._receipt_facts) if self._receipt_facts is not None else None
        self._receipt_bytes, self._receipt_facts = _atomic_write(
            self._output_root, self._output.name, value, expected, check=self._check_context)

    def record(self, phase, *, account=None, ack=None, readback=None, reason=None):
        try:
            with self._lock:
                if (phase not in _PHASES or (reason is not None and reason not in _REASONS)
                        or ('phase' in self._value
                            and _PHASES.index(phase) < _PHASES.index(self._value['phase']))):
                    _fail()
                value = dict(self._value, phase=phase)
                if account is not None:
                    identity = _account(account)
                    if value.get('account') is not None and value['account'] != identity:
                        _fail()
                    value['account'] = identity
                # Before the first durable attempt marker, a proven wrong
                # account must stop the caller before it can write externally.
                # After that marker, retain newly observed wrong-account
                # evidence: rejection cannot undo an external submission.
                started = self._value.get('phase') in ('submit_attempted', 'platform_ack', 'readback')
                if (not started and value.get('account') is not None
                        and value['account']['id'] != self._base['approvedAccountId']):
                    _fail()
                if ack is not None:
                    observation = _ack(ack)
                    if value.get('ack') is not None and any(value['ack'][key] != observation[key]
                            for key in ('kind', 'accepted', 'id', 'source')):
                        _fail()
                    value['ack'] = observation
                if readback is not None:
                    value['readback'] = _readback(readback)
                value.pop('reason', None)
                if reason is not None:
                    value['reason'] = reason
                self._write(value)
                self._value = value
        except PublicationReceiptError:
            raise
        except Exception:
            # No exception from filesystem/provider-supplied objects is echoed.
            _fail()
        return None
