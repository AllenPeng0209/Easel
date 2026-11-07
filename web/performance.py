"""Bounded first-party latency measurements; no URLs, prompts, or credentials."""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path
import sqlite3
import statistics
import time
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class Metric(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    metric: Literal['api', 'fcp', 'lcp', 'interaction', 'video_metadata', 'video_first_frame', 'video_stall', 'job_accept', 'job_complete']
    label: str = Field(default='document', pattern=r'^[a-z][a-z0-9_.-]{0,63}$')
    value_ms: float = Field(ge=0, le=14400000)
    network: Literal['unknown', 'slow-2g', '2g', '3g', '4g'] = 'unknown'
    device: Literal['desktop', 'mobile'] = 'desktop'


class MetricBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    metrics: list[Metric] = Field(max_length=30)


class PerformanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        started, request_id = time.perf_counter(), uuid.uuid4().hex
        scope.setdefault('state', {})['request_id'] = request_id

        async def measured(message):
            if message['type'] == 'http.response.start':
                elapsed = (time.perf_counter() - started) * 1000
                headers = list(message.get('headers', []))
                previous = [value for key, value in headers if key.lower() == b'server-timing']
                headers = [(key, value) for key, value in headers if key.lower() not in {b'server-timing', b'x-request-id'}]
                timing = b', '.join([*previous, f'app;dur={elapsed:.2f}'.encode()])
                headers.extend([(b'server-timing', timing), (b'x-request-id', request_id.encode())])
                message = {**message, 'headers': headers}
            await send(message)

        await self.app(scope, receive, measured)


class PerformanceStore:
    def __init__(self, root: Path, *, limit=5000):
        directory = Path(root) / '.easel'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path, self.limit = directory / 'performance.sqlite3', limit
        with self._connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS measurements '
                '(id INTEGER PRIMARY KEY,at INTEGER NOT NULL,actor TEXT NOT NULL,project TEXT NOT NULL,'
                'metric TEXT NOT NULL,label TEXT NOT NULL,value REAL NOT NULL,network TEXT NOT NULL,device TEXT NOT NULL)')
            connection.execute('CREATE INDEX IF NOT EXISTS performance_project ON measurements(project,id)')
        self.path.chmod(0o600)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=3)

    def record(self, actor, project, values):
        now = int(time.time() * 1000)
        with self._connect() as connection:
            connection.executemany('INSERT INTO measurements(at,actor,project,metric,label,value,network,device) VALUES(?,?,?,?,?,?,?,?)',
                [(now, actor, project, row['metric'], row['label'], row['value_ms'], row.get('network', 'unknown'), row.get('device', 'desktop')) for row in values])
            connection.execute('DELETE FROM measurements WHERE id <= (SELECT COALESCE(MAX(id),0)-? FROM measurements)', (self.limit,))

    def summary(self, project):
        groups = defaultdict(list)
        with self._connect() as connection:
            for metric, label, value, network, device in connection.execute(
                'SELECT metric,label,value,network,device FROM measurements WHERE project=? ORDER BY id', (project,)):
                groups[(metric, label, network, device)].append(value)
        result = []
        for (metric, label, network, device), values in sorted(groups.items()):
            values.sort()
            result.append({'metric': metric, 'label': label, 'network': network, 'device': device, 'count': len(values),
                           'p50_ms': statistics.median(values), 'p95_ms': values[max(0, math.ceil(len(values)*.95)-1)], 'max_ms': values[-1]})
        return result


def create_router(get_root, require_admin):
    from easel.projects import project_id
    from easel.request_identity import current_user
    router = APIRouter()

    @router.post('/api/performance/metrics')
    def metrics(payload: MetricBatch):
        actor = current_user()
        if not actor:
            raise HTTPException(401, '请先登录')
        PerformanceStore(get_root()).record(actor['id'], project_id(), [row.model_dump() for row in payload.metrics])
        return {'accepted': len(payload.metrics)}

    @router.get('/api/performance/summary')
    def summary():
        require_admin()
        return {'scope': project_id(), 'maximumSamples': 5000, 'metrics': PerformanceStore(get_root()).summary(project_id())}

    return router
