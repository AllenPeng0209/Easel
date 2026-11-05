"""A narrowly scoped, temporary pause for direct publication HTTP writes.

Only a valid private Host flag is actionable. Missing, malformed or unsafe
sources are ignored rather than guessing an actor/platform or changing the
existing authorization policy. Native workflow callbacks do not use this guard.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from fastapi import HTTPException

_FILENAME = 'operator-http-pause.json'
_LIMIT = 4096


def _facts(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private_regular(info):
    return (stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1 and 0 < info.st_size <= _LIMIT
            and (not hasattr(os, 'geteuid') or info.st_uid == os.geteuid()))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate flag field')
        result[key] = value
    return result


def _read_pause(directory):
    directory_fd = descriptor = None
    try:
        root = Path(directory)
        if not root.is_absolute() or root.resolve(strict=True) != root:
            return None
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NONBLOCK', 0)
        relative = os.open in os.supports_dir_fd and hasattr(os, 'O_DIRECTORY') and hasattr(os, 'O_NOFOLLOW')
        if relative:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
            directory_fd = os.open(root.anchor, directory_flags)
            for part in root.parts[1:]:
                following = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = following
            before = os.stat(_FILENAME, dir_fd=directory_fd, follow_symlinks=False)
            if not _private_regular(before):
                return None
            descriptor = os.open(_FILENAME, flags, dir_fd=directory_fd)
        else:
            before = (root / _FILENAME).lstat()
            if not _private_regular(before):
                return None
            descriptor = os.open(root / _FILENAME, flags)
        opened = os.fstat(descriptor)
        if not _private_regular(opened) or _facts(opened) != _facts(before):
            return None
        raw = os.read(descriptor, _LIMIT + 1)
        after = (os.stat(_FILENAME, dir_fd=directory_fd, follow_symlinks=False) if relative
                 else (root / _FILENAME).lstat())
        if (len(raw) != opened.st_size or _facts(os.fstat(descriptor)) != _facts(opened)
                or _facts(after) != _facts(opened) or root.resolve(strict=True) != root
                or relative and (root.stat().st_dev, root.stat().st_ino)
                != (os.fstat(directory_fd).st_dev, os.fstat(directory_fd).st_ino)):
            return None
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
        if (not isinstance(value, dict) or set(value) != {'version', 'actorId', 'platform'}
                or type(value['version']) is not int or value['version'] != 1
                or not isinstance(value['actorId'], str) or not 1 <= len(value['actorId']) <= 256
                or any(ord(character) < 32 or ord(character) == 127 for character in value['actorId'])
                or not isinstance(value['platform'], str)
                or re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value['platform']) is None):
            return None
        return value
    except (OSError, ValueError, UnicodeError, TypeError, RuntimeError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_fd is not None:
            os.close(directory_fd)


def require_operator_http_available(directory, *, actor_id, platform):
    pause = _read_pause(directory)
    if pause is not None and pause['actorId'] == actor_id and pause['platform'] == platform:
        raise HTTPException(409, '排查中，当前平台的直接发布操作暂时暂停。')
