"""Bounded admission for native AI work in the single-process Web host.

The host, not a browser payload, supplies owner/session/turn identity. Limits
are process-local and event-loop-local; multiple workers/replicas need a shared
scheduler. Durable turn idempotency belongs to the host journal; this module
rejects duplicate in-flight turns. Keep its lease around the background task,
not the SSE consumer: a browser disconnect must not release a running slot.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
import inspect
import re
import time
from typing import Any
from weakref import WeakKeyDictionary


_ERRORS = {
    'duplicate_turn': (409, '这条 AI 任务已在执行或排队，请等待原任务结果。'),
    'queue_full': (429, '服务器的 AI 任务队列已满，请稍后重试。'),
    'owner_queue_full': (429, '你的 AI 排队任务已达上限，请先等待或取消已有排队任务。'),
    'wait_timeout': (429, 'AI 任务等待资源超时，尚未开始执行，请稍后重试。'),
    'config_invalid': (503, '服务器的 AI 并发限制配置无效，尚未开始执行。'),
    'config_changed': (503, 'AI 资源限制正在调整，请等待当前任务结束后重试。'),
    'identity_invalid': (503, '服务器未提供有效的 AI 任务身份，尚未开始执行。'),
}


class AgentCapacityError(Exception):
    """A public error containing no caller-provided identity or value."""

    def __init__(self, code: str):
        self.status_code, self.message = _ERRORS[code]
        self.code = code
        super().__init__(self.message)

    def as_dict(self) -> dict:
        return {'code': self.code, 'httpStatus': self.status_code, 'message': self.message}


def _integer(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name, str(default))
    if not isinstance(raw, str) or not re.fullmatch(r'[0-9]{1,5}', raw.strip()):
        raise AgentCapacityError('config_invalid')
    number = int(raw.strip())
    if not low <= number <= high:
        raise AgentCapacityError('config_invalid')
    return number


@dataclass(frozen=True)
class _Limits:
    running: int
    pending: int
    pending_per_owner: int
    wait_seconds: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> _Limits:
        return cls(
            _integer(env, 'EASEL_AGENT_MAX_CONCURRENT', 0, 0, 32),
            _integer(env, 'EASEL_AGENT_MAX_PENDING', 4, 0, 256),
            _integer(env, 'EASEL_AGENT_MAX_PENDING_PER_OWNER', 1, 1, 16),
            _integer(env, 'EASEL_AGENT_WAIT_TIMEOUT_SECONDS', 600, 1, 7200),
        )


@dataclass(frozen=True)
class AgentLease:
    limited: bool
    waited: bool
    wait_seconds: float


_Key = tuple[str, str, str]


@dataclass(eq=False)
class _Waiter:
    key: _Key
    ready: asyncio.Future


class _Gate:
    def __init__(self, limits: _Limits):
        self.limits = limits
        self.active: set[_Key] = set()
        self.waiting: deque[_Waiter] = deque()

    @property
    def occupied(self) -> bool:
        return bool(self.active or self.waiting)

    def _dispatch(self) -> None:
        # No await: new arrivals cannot jump ahead during a queued handoff.
        while self.waiting and len(self.active) < self.limits.running:
            waiter = self.waiting.popleft()
            if waiter.ready.cancelled():
                continue
            self.active.add(waiter.key)
            waiter.ready.set_result(None)

    def release(self, key: _Key) -> None:
        self.active.discard(key)
        self._dispatch()

    def _withdraw(self, waiter: _Waiter) -> None:
        if waiter in self.waiting:
            self.waiting.remove(waiter)
        else:
            # Cancellation may race with a grant before user work starts.
            self.active.discard(waiter.key)
        if not waiter.ready.done():
            waiter.ready.cancel()
        self._dispatch()

    async def acquire(self, key: _Key, on_queued: Callable | None) -> AgentLease:
        if key in self.active or any(item.key == key for item in self.waiting):
            raise AgentCapacityError('duplicate_turn')
        if not self.waiting and len(self.active) < self.limits.running:
            self.active.add(key)
            return AgentLease(True, False, 0.0)
        if len(self.waiting) >= self.limits.pending:
            raise AgentCapacityError('queue_full')
        if sum(item.key[0] == key[0] for item in self.waiting) >= self.limits.pending_per_owner:
            raise AgentCapacityError('owner_queue_full')

        waiter = _Waiter(key, asyncio.get_running_loop().create_future())
        self.waiting.append(waiter)
        notice = {'position': len(self.waiting), 'running': len(self.active),
                  'waiting': len(self.waiting), 'max_running': self.limits.running,
                  'max_waiting': self.limits.pending}
        started = time.monotonic()

        async def wait_for_admission():
            if on_queued is not None:
                result = on_queued(notice)
                if inspect.isawaitable(result):
                    await result
            await asyncio.shield(waiter.ready)

        try:
            # The deadline also bounds an asynchronous notification callback.
            await asyncio.wait_for(wait_for_admission(), self.limits.wait_seconds)
        except asyncio.TimeoutError:
            self._withdraw(waiter)
            raise AgentCapacityError('wait_timeout') from None
        except BaseException:
            self._withdraw(waiter)
            raise
        return AgentLease(True, True, time.monotonic() - started)


# Do not retain the loop in an idle gate; weak keys support short-lived loops.
_GATES: WeakKeyDictionary = WeakKeyDictionary()


def _gate(limits: _Limits) -> _Gate | None:
    loop = asyncio.get_running_loop()
    gate = _GATES.get(loop)
    if gate is not None and gate.limits != limits:
        if gate.occupied:
            raise AgentCapacityError('config_changed')
        del _GATES[loop]
        gate = None
    if limits.running == 0:
        return None
    if gate is None:
        gate = _Gate(limits)
        _GATES[loop] = gate
    return gate


def _identity(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)):
        raise AgentCapacityError('identity_invalid')
    return value


@asynccontextmanager
async def agent_slot(env: Mapping[str, str], *, owner_id: str, session_id: str,
                     turn_id: str, on_queued: Callable | None = None):
    """Reserve capacity for the whole real task; release on exit/cancellation.

    MAX_CONCURRENT defaults to 0 (disabled/local-compatible). Pending limits
    count only waiting tasks; a running owner may enqueue one further task under
    the default. The notice reports position at enqueue, not a live position.
    Never tie this context's lifetime to a client's connection/response stream.
    """
    gate = _gate(_Limits.from_env(env))
    if gate is None:
        yield AgentLease(False, False, 0.0)
        return
    key = (_identity(owner_id), _identity(session_id), _identity(turn_id))
    lease = await gate.acquire(key, on_queued)
    try:
        yield lease
    finally:
        gate.release(key)
