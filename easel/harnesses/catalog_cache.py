"""Opt-in, account/version-bound protection for read-only Codex catalog probes.

Only opaque fingerprints and bounded catalog JSON are retained. One worker owns
all enabled probes, including callers on different asyncio loops. A caller's
cancellation never cancels another caller's shared probe.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
import tomllib
import uuid

from .base import HarnessError

_MAX_CACHE_ENTRIES = 16
_MAX_INFLIGHT = 16
_MAX_WAITERS = 32
_MAX_FILE_BYTES = 1024 * 1024
_MAX_FILE_COUNT = 128
_MAX_RESULT_BYTES = 1024 * 1024
_PROBE_SECONDS = 25
_now = time.monotonic
_FILE_AUTH_HOST = sys.platform.startswith('linux')
_SYSTEM_CONFIGS = (
    Path('/etc/codex/config.toml'), Path('/etc/codex/managed_config.toml'),
    Path('/etc/codex/requirements.toml'),
    Path('/Library/Preferences/com.openai.codex.plist'),
)


def catalog_ttl(env: dict[str, str]) -> int:
    raw = env.get('EASEL_CODEX_CATALOG_CACHE_TTL_SECONDS', '0')
    try:
        ttl = int(raw)
        if str(ttl) != str(raw).strip() or not 0 <= ttl <= 300:
            raise ValueError
    except (ValueError, TypeError):
        raise HarnessError('模型目录缓存 TTL 须为 0–300 整数秒') from None
    return ttl


@dataclass(frozen=True)
class _Identity:
    digest: str
    reusable: bool


def _identity(request, command, include_config) -> _Identity | None:
    """Hash bounded local versions; unknown auth/config layers disable sharing."""
    try:
        from easel.projects import project_id
        from easel.request_identity import current_user
        home = Path(request.env.get('HOME') or Path.home())
        codex_home = Path(request.env.get('CODEX_HOME') or home / '.codex')
        cwd = request.cwd.resolve()
        if not home.is_absolute() or not codex_home.is_absolute():
            return None
        actor = current_user()
        h = hashlib.sha256(json.dumps([str(request.cwd), str(cwd), command, include_config,
            sorted(request.env.items()), getattr(os, 'getuid', lambda: None)(),
            actor.get('id') if actor else None, project_id()],
            ensure_ascii=False, allow_nan=False).encode())
        reusable = _FILE_AUTH_HOST
        explicit_file_store = False
        files = set(_SYSTEM_CONFIGS)
        files.update((codex_home / name) for name in ('config.toml', 'auth.json',
                                                     'managed_config.toml', 'requirements.toml'))
        # Profiles are separate *.config.toml files in recent Codex versions.
        for path in codex_home.glob('*.toml'):
            files.add(path)
            if len(files) > _MAX_FILE_COUNT:
                return None
        ancestors = (cwd, *cwd.parents)
        if len(ancestors) > 64:
            return None
        files.update(parent / '.codex' / 'config.toml' for parent in ancestors)
        for argument in command:
            p = Path(argument)
            if p.is_absolute() and p.exists():
                resolved = p.resolve()
                info = resolved.stat()
                h.update(json.dumps([str(p), str(resolved), info.st_dev, info.st_ino,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns]).encode())
                files.update(parent / 'package.json' for parent in resolved.parents[:4])
        pending = sorted(files, key=str)
        visited = set()
        total = 0
        while pending:
            path = pending.pop(0)
            if str(path) in visited:
                continue
            visited.add(str(path))
            if len(visited) > _MAX_FILE_COUNT:
                return None
            h.update(str(path).encode())
            try:
                info = path.stat()
            except FileNotFoundError:
                h.update(b'\0missing')
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
                return None
            h.update(json.dumps([str(path.resolve()), info.st_dev, info.st_ino,
                info.st_mode, info.st_uid, info.st_gid, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns]).encode())
            # O_NONBLOCK prevents a replaced FIFO from blocking fingerprinting.
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    return None
                raw = stream.read(_MAX_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
            if (len(raw) > _MAX_FILE_BYTES or opened.st_ino != info.st_ino
                    or opened.st_mtime_ns != after.st_mtime_ns
                    or opened.st_ctime_ns != after.st_ctime_ns):
                return None
            total += len(raw)
            if total > 4 * _MAX_FILE_BYTES:
                return None
            h.update(hashlib.sha256(raw).digest())
            if path.suffix == '.plist':
                reusable = False  # MDM precedence is owned by Codex, not this cache.
            if path.suffix != '.toml':
                continue
            config = tomllib.loads(raw.decode('utf-8'))
            if path == codex_home / 'config.toml':
                explicit_file_store = config.get('cli_auth_credentials_store') == 'file'
            def inspect(value):
                nonlocal reusable
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == 'cli_auth_credentials_store' and item != 'file':
                            reusable = False
                        if key == 'model_provider' and item != 'openai':
                            reusable = False  # External credential chains lack a local version.
                        if key in ('auth', 'auth_command', 'gateway_oauth', 'credential_process'):
                            reusable = False
                        if key in ('config_file', 'model_catalog_json') and isinstance(item, str):
                            if len(pending) + len(visited) >= _MAX_FILE_COUNT or item.startswith('~') and not item.startswith('~/'):
                                raise ValueError('Unversioned configuration dependency')
                            external = home / item[2:] if item.startswith('~/') else Path(item)
                            pending.append(external if external.is_absolute() else path.parent / external)
                        if key in ('include', 'includes', 'managed_config_path', 'config_server'):
                            reusable = False
                        inspect(item)
                elif isinstance(value, list):
                    for item in value:
                        inspect(item)
            inspect(config)
        return _Identity(h.hexdigest(), reusable and explicit_file_store)
    except (OSError, RuntimeError, ValueError, TypeError):
        return None  # Still allow an uncached, serialized native diagnostic.


@dataclass
class _Flight:
    future: Future
    deadline: float
    waiters: int = 0


class _CatalogCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.flights = {}
        self.waiters = 0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='codex-catalog')

    def _finish(self, key, flight, raw=None, error=None, *, ttl=0, reusable=False):
        with self.lock:
            if self.flights.get(key) is flight:
                self.flights.pop(key)
                if error is None and reusable:
                    self.cache[key] = (_now() + ttl, raw)
                    self.cache.move_to_end(key)
                    while len(self.cache) > _MAX_CACHE_ENTRIES:
                        self.cache.popitem(last=False)
        # Future callbacks may run synchronously: never settle under our lock.
        if error is not None:
            flight.future.set_exception(error)
        else:
            flight.future.set_result(raw)

    def _probe(self, key, flight, identity, version, factory, ttl):
        with self.lock:
            abandoned = flight.waiters == 0
            if abandoned and self.flights.get(key) is flight:
                self.flights.pop(key)
        if abandoned:
            flight.future.cancel()
            return
        try:
            if version() != identity:
                raise HarnessError('模型目录读取期间登录或配置已变化，请重试')
            remaining = flight.deadline - _now()
            if remaining <= 0:
                raise HarnessError('模型目录探测排队超时，请稍后重试')
            async def run():
                return await asyncio.wait_for(factory(), remaining)
            result = asyncio.run(run())
            if version() != identity:
                raise HarnessError('模型目录读取期间登录或配置已变化，请重试')
            raw = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
            if len(raw) > _MAX_RESULT_BYTES:
                raise HarnessError('模型目录过大，请检查原生配置')
        except BaseException as error:
            if isinstance(error, TimeoutError):
                error = HarnessError('模型目录探测超时，请稍后重试')
            elif not isinstance(error, Exception):
                error = HarnessError('模型目录探测已中止，请重试')
            self._finish(key, flight, error=error)
        else:
            self._finish(key, flight, raw, ttl=ttl,
                         reusable=identity is not None and identity.reusable)

    async def get(self, version, factory, ttl):
        with self.lock:
            if self.waiters >= _MAX_WAITERS:
                raise HarnessError('模型目录请求过多，请稍后重试')
            self.waiters += 1
        flight = None
        key = None
        try:
            identity = await asyncio.to_thread(version)
            key = identity.digest if identity and identity.reusable else uuid.uuid4().hex
            created = False
            with self.lock:
                now = _now()
                for expired in [key for key, item in self.cache.items() if item[0] <= now]:
                    self.cache.pop(expired)
                cached = self.cache.get(key)
                if cached:
                    self.cache.move_to_end(key)
                    raw = cached[1]
                else:
                    flight = self.flights.get(key)
                    if flight is None:
                        if len(self.flights) >= _MAX_INFLIGHT:
                            raise HarnessError('模型目录探测队列已满，请稍后重试')
                        flight = _Flight(Future(), now + _PROBE_SECONDS)
                        self.flights[key] = flight
                        created = True
                    flight.waiters += 1
            if flight is not None:
                if created:
                    context = copy_context()
                    try:
                        self.executor.submit(context.run, self._probe, key, flight,
                                             identity, version, factory, ttl)
                    except Exception:
                        self._finish(key, flight, error=HarnessError('模型目录探测暂不可启动，请重试'))
                wrapped = asyncio.wrap_future(flight.future)
                wrapped.add_done_callback(lambda future: None if future.cancelled() else future.exception())
                raw = await asyncio.wait_for(asyncio.shield(wrapped), _PROBE_SECONDS + 3)
            if await asyncio.to_thread(version) != identity:
                raise HarnessError('模型目录读取期间登录或配置已变化，请重试')
            value = json.loads(raw)  # Each caller receives independent containers.
            return value[0], value[1]
        finally:
            with self.lock:
                self.waiters -= 1
                if flight is not None:
                    flight.waiters -= 1
                    # Keep the slot until the worker consumes this work item.
                    # Future.cancel() leaves executor queue items allocated and
                    # would let repeated enqueue/cancel bypass the memory bound.


_CATALOGS = _CatalogCache()


async def protected_catalog(request, command, include_config, factory, ttl):
    return await _CATALOGS.get(lambda: _identity(request, command, include_config), factory, ttl)
