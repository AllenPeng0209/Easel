"""Lifecycle-owned polling for explicitly enabled interaction rules.

The store owns persistent deduplication and send state. This loop only asks it
to process eligible work; it never interprets messages or retries sends itself.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable


class InteractionWorker:
    def __init__(self, tick: Callable, *, interval: float = 15):
        if interval <= 0:
            raise ValueError('Worker interval must be positive')
        self.tick = tick
        self.interval = interval
        self.last_error = ''
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name='easel-interaction-worker')

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            # Finish an already claimed operation so its receipt is durable.
            await self._task
            self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
                self.last_error = ''
            except Exception:
                self.last_error = '自动处理暂时不可用，原消息和发送记录已保留。'
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                pass
