"""Private loopback connection to the pinned, local BeefTV workspace.

Credentials stay in the runtime's 0600 connection file. Business mutations use
the caller's stable operation id and are never automatically retried.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit

import httpx
from easel.projects import project_id, workspace as project_workspace

ROOT = Path(__file__).resolve().parents[1]
# Explicit migration access only; active IPs never fall back to this instance.
CONNECTION = ROOT / '.scratch' / 'beeftv' / 'run' / 'client.json'
UI_URL = 'http://127.0.0.1:8843'
VERSION = '2.1.1'
READ_OPERATIONS = {'canvas.get', 'canvas.search', 'asset.get', 'asset.list',
                   'task.get', 'model.catalog', 'skill.catalog', 'skill.get',
                   'skill.file', 'project.canvas.search', 'project.media.search',
                   'media.overview', 'media.inspect', 'media.check',
                   'canvas.generation.propose'}


class BeefTVError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class BeefTVClient:
    def __init__(self, base_url: str, client_id: str, client_token: str, *, transport=None,
                 ui_url: str = '', read_only: bool = False, before_request=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip('/') != '/api'):
            raise ValueError('BeefTV must use the private loopback API')
        self.base_url = base_url.rstrip('/')
        self.headers = {'X-Beeftv-Client': client_id, 'Authorization': f'Bearer {client_token}'}
        self.transport = transport
        self.ui_url = ui_url
        self.read_only = read_only
        self.before_request = before_request

    def _authorize(self, method: str):
        if self.read_only and method.upper() not in {'GET', 'HEAD'}:
            raise BeefTVError('旧创作服务仅用于读取已归属的历史交付', 403)
        if self.before_request is not None:
            self.before_request()

    def request(self, method: str, path: str, *, body=None, timeout=15):
        if not path.startswith('/') or '..' in path or '?' in path or '#' in path:
            raise ValueError('Invalid BeefTV API path')
        self._authorize(method)
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, transport=self.transport,
                              timeout=timeout, headers=self.headers) as client:
                response = client.request(method, self.base_url + path, json=body)
        except httpx.HTTPError:
            raise BeefTVError('本机视频创作服务暂时不可用') from None
        if not response.is_success:
            message = '画布版本已变化，请重新读取后再修改' if response.status_code == 409 else '本机视频创作服务请求未完成'
            raise BeefTVError(message, response.status_code)
        try:
            payload = response.json()
        except ValueError:
            raise BeefTVError('本机视频创作服务返回了无效响应') from None
        if not isinstance(payload, dict):
            raise BeefTVError('本机视频创作服务返回了无效响应')
        if payload.get('code', 0) not in (0, 200):
            raise BeefTVError('本机视频创作服务未接受该操作')
        return payload.get('data', payload)

    def operation(self, name: str, params: dict, *, operation_id: str | None = None):
        if not re.fullmatch(r'[a-z][a-z0-9_.]{1,79}', name):
            raise ValueError('Invalid business operation')
        if name not in READ_OPERATIONS and not operation_id:
            raise ValueError('A stable operation_id is required for a mutation')
        if operation_id and not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', operation_id):
            raise ValueError('Invalid operation_id')
        body = {'params': params}
        if name not in READ_OPERATIONS:
            body['opId'] = operation_id
        result = self.request('POST', '/ops/' + name, body=body, timeout=120)
        return result.get('result', result) if isinstance(result, dict) else result

    def create_canvas(self, project: dict) -> str:
        self._authorize('PUT')
        project_id = project['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', project_id):
            raise ValueError('Invalid workshop project id')
        canvas_id = 'easel-' + project_id
        try:
            self.request('GET', '/canvas-projects/' + canvas_id)
            return canvas_id
        except BeefTVError as error:
            if error.status != 404:
                raise
        document = {'id': canvas_id, 'title': project['title'], 'nodes': [],
                    'connections': [], 'revision': 0, 'viewport': {'x': 0, 'y': 0, 'k': 1}}
        self.request('PUT', '/canvas-projects/' + canvas_id, body={'project': document})
        return canvas_id

    def download_resource(self, resource_id: str, destination: Path) -> Path:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', resource_id):
            raise ValueError('Invalid resource id')
        self._authorize('GET')
        destination = Path(destination)
        if destination.is_symlink() or destination.parent.is_symlink():
            raise ValueError('Resource destination cannot be a symlink')
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.partial')
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, transport=self.transport,
                              timeout=120, headers=self.headers) as client:
                with client.stream('GET', self.base_url + '/resources/' + resource_id + '/file') as response:
                    if not response.is_success:
                        raise BeefTVError('成片资源尚未取回', response.status_code)
                    size = 0
                    with partial.open('xb') as output:
                        for block in response.iter_bytes(1024 * 1024):
                            size += len(block)
                            if size > 512 * 1024 * 1024:
                                raise BeefTVError('成片超过本机收取大小限制')
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                    if not size:
                        raise BeefTVError('成片资源为空，尚未完成交付')
            os.replace(partial, destination)
            return destination
        except httpx.HTTPError:
            raise BeefTVError('成片下载未完成，请收取原任务结果') from None
        finally:
            partial.unlink(missing_ok=True)

    def status(self) -> dict:
        result = {'state': 'unavailable', 'ui_url': self.ui_url, 'version': VERSION,
                  'message': '请启动本机视频创作服务'}
        try:
            self.request('GET', '/ops', timeout=2)
        except BeefTVError:
            return result
        return {**result, 'state': 'running', 'message': 'BeefTV 创作服务已连接'}


def _runtime_paths(repository: Path | None = None, workspace: Path | None = None):
    from easel.beeftv_runtime import runtime_paths

    repository = Path(repository or ROOT)
    return runtime_paths(repository, workspace or project_workspace(repository))


def client_for_workspace(repository: Path, workspace: Path, *, start: bool = False) -> BeefTVClient:
    from easel.beeftv_runtime import ensure_started, owned_runtime, read_connection

    try:
        paths = _runtime_paths(repository, workspace)
        if start:
            ensure_started(paths)
        data = read_connection(paths)
        if not data:
            raise ValueError('unconfigured')
    except (OSError, KeyError, ValueError, RuntimeError):
        raise BeefTVError('当前 IP 的视频创作服务尚未连接', 503) from None

    def require_owned_backend():
        if not owned_runtime(paths, component='backend'):
            raise BeefTVError('当前 IP 的视频创作服务尚未启动', 503)

    return BeefTVClient(data['base_url'], data['client_id'], data['client_token'],
                        ui_url=data['ui_url'], before_request=require_owned_backend)


def configured_client() -> BeefTVClient:
    return client_for_workspace(ROOT, project_workspace(ROOT))


def legacy_client(repository: Path | None = None) -> BeefTVClient:
    """Explicit, read-only access to the preserved pre-isolation runtime."""
    path = Path(repository or ROOT) / '.scratch/beeftv/run/client.json'
    try:
        if path.is_symlink():
            raise ValueError
        data = json.loads(path.read_text(encoding='utf-8'))
        return BeefTVClient(data['base_url'], data['client_id'], data['client_token'],
                            read_only=True)
    except (OSError, KeyError, TypeError, ValueError):
        raise BeefTVError('历史视频创作服务暂不可读取', 503) from None


def runtime_status() -> dict:
    try:
        from easel.beeftv_runtime import owned_runtime

        result = configured_client().status()
        if not owned_runtime(_runtime_paths(), component='frontend'):
            return {'state': 'unavailable', 'ui_url': '', 'version': VERSION,
                    'message': '当前 IP 的视频画布尚未启动'}
        return result
    except (BeefTVError, ImportError, OSError, ValueError, RuntimeError):
        return {'state': 'unavailable', 'ui_url': '', 'version': VERSION,
                'message': '当前 IP 的视频创作服务尚未连接'}


def create_canvas(project: dict) -> str:
    try:
        from easel.beeftv_runtime import ensure_started

        ensure_started(_runtime_paths())
    except (ImportError, OSError, ValueError, RuntimeError):
        raise BeefTVError('当前 IP 的视频创作服务尚未启动', 503) from None
    return configured_client().create_canvas(project)


def canvas_state(project: dict) -> dict:
    canvas_id = project.get('beeftv_canvas_id')
    if not canvas_id:
        return {}
    result = configured_client().operation('canvas.get', {'canvasId': canvas_id})
    canvas = result.get('canvas', {})
    if not isinstance(canvas.get('revision'), int):
        raise BeefTVError('无法核实当前画布版本')
    return {'revision': canvas['revision']}


def _task_id(receipt: dict) -> str:
    task_id = receipt.get('task_id', '')
    if not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id):
        raise BeefTVError('渲染回执缺少真实任务编号')
    return task_id


def _render_progress(result: dict) -> dict[str, float]:
    reported = result.get('progress')
    if isinstance(reported, bool):
        return {}
    try:
        progress = float(reported)
    except (TypeError, ValueError, OverflowError):
        return {}
    if not 0 <= progress <= 100:
        return {}
    return {'progress': progress / 100 if progress > 1 else progress}


def render_start(project: dict, operation_id: str) -> dict:
    revision = project.get('engine_current_revision')
    if type(revision) is not int or revision < 0 or not project.get('beeftv_canvas_id'):
        raise BeefTVError('请先连接并读取当前项目画布', 422)
    result = configured_client().operation('canvas.timeline.render', {
        'canvasId': project['beeftv_canvas_id'], 'expectedRevision': revision,
    }, operation_id=operation_id)
    if result.get('sourceRevision') != revision or not result.get('taskId'):
        raise BeefTVError('未能确认本次渲染的来源版本')
    return {'task_id': result['taskId'], 'engine_revision': result['sourceRevision'],
            'status': result.get('status', 'queued'), **_render_progress(result)}


def _render_task_observation(task: dict) -> dict:
    state = task.get('status')
    if state in ('in_progress', 'processing'):
        state = 'running'
    if state not in ('queued', 'running', 'succeeded', 'failed', 'cancelled'):
        raise BeefTVError('渲染状态无法确认，请查询原任务', 503)
    return {'status': state, **_render_progress(task)}


def render_status(project: dict, receipt: dict) -> dict:
    _, task = _render_task_with_source(project, receipt)
    return _render_task_observation(task)


def _legacy_receipt_owned(project: dict, receipt: dict) -> bool:
    """Only an explicit current-IP migration binding permits an old task read."""
    identifier = project.get('id')
    canvas_id = project.get('beeftv_canvas_id')
    task_id = receipt.get('task_id')
    operation_id = receipt.get('operation_id')
    revision = receipt.get('engine_revision')
    if (not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier)
            or not isinstance(canvas_id, str) or canvas_id != receipt.get('canvas_id')
            or not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,160}', task_id)
            or not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', operation_id)
            or type(revision) is not int or revision < 0):
        return False
    workspace = project_workspace(ROOT).resolve()
    path = workspace / '.easel/beeftv/run/migration.json'
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(workspace)):
        return False
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return False
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    if (not isinstance(manifest, dict) or type(manifest.get('version')) is not int
            or manifest['version'] != 1 or manifest.get('project_id') != project_id()
            or manifest.get('workspace') != str(workspace)
            or not isinstance(manifest.get('legacy_renders'), list)):
        return False
    expected = {'local_project_id': identifier, 'canvas_id': canvas_id, 'task_id': task_id,
                'operation_id': operation_id, 'engine_revision': revision}
    return any(isinstance(row, dict) and type(row.get('engine_revision')) is int
               and all(row.get(key) == value for key, value in expected.items())
               for row in manifest['legacy_renders'])


def _render_task_with_source(project: dict, receipt: dict):
    client = configured_client()
    task_id = _task_id(receipt)
    try:
        return client, client.request('GET', '/tasks/' + task_id)
    except BeefTVError as error:
        if error.status != 404 or not _legacy_receipt_owned(project, receipt):
            raise
    historical = legacy_client(ROOT)
    task = historical.request('GET', '/tasks/' + task_id)
    _bound_cancel_observation(task, project, receipt, task_id)
    return historical, task


def _bound_cancel_observation(task, project: dict, receipt: dict, task_id: str) -> dict:
    # canvas.timeline.render stores this operation prefix on its local task.
    canvas_id = project.get('beeftv_canvas_id')
    operation = receipt.get('operation_id')
    if (not isinstance(task, dict) or not canvas_id or canvas_id != receipt.get('canvas_id')
            or not isinstance(operation, str) or not operation or task.get('id') != task_id
            or task.get('type') != 'timeline_render' or task.get('projectId') != canvas_id
            or task.get('clientOperationId') != 'assistant-render:' + operation):
        raise BeefTVError('无法确认原渲染任务身份', 409)
    return {'task_id': task_id, **_render_task_observation(task)}


def render_cancel_status(project: dict, receipt: dict) -> dict:
    """Read back one verified local render after an uncertain cancellation."""
    task_id = _task_id(receipt)
    task = configured_client().request('GET', '/tasks/' + task_id)
    return _bound_cancel_observation(task, project, receipt, task_id)


def render_cancel(project: dict, receipt: dict, *, before_post=None) -> dict:
    """Request cancellation once for the exact bound active timeline task."""
    client = configured_client()
    task_id = _task_id(receipt)
    path = '/tasks/' + task_id
    current = _bound_cancel_observation(client.request('GET', path), project, receipt, task_id)
    if current['status'] not in ('queued', 'running'):
        # BeefTV's cancel endpoint can pause delivery of a succeeded task.
        return current
    if before_post is not None:
        before_post()
    result = client.request('POST', path + '/cancel', body={})
    return _bound_cancel_observation(result, project, receipt, task_id)


def render_collect(project: dict, receipt: dict, outputs_root: Path | None = None) -> dict:
    client, task = _render_task_with_source(project, receipt)
    task_id = _task_id(receipt)
    if task.get('status') != 'succeeded':
        raise BeefTVError('该渲染尚未成功，不能登记交付', 409)
    try:
        result = task.get('resultJson', {})
        if isinstance(result, str):
            result = json.loads(result)
        resource_id = result.get('resourceId') or result.get('video', {}).get('resourceId')
        if not resource_id:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise BeefTVError('成功任务尚未提供可收取的成片资源') from None
    project_id = project.get('id', '')
    if not re.fullmatch(r'[a-f0-9]{32}', project_id):
        raise BeefTVError('视频项目编号无效')
    outputs = Path(outputs_root or project_workspace(ROOT) / 'outputs').resolve()
    directory = outputs
    for part in (f'视频工坊-{project_id[:8]}', 'assets', 'renders'):
        directory /= part
        if directory.is_symlink():
            raise BeefTVError('成片目录不能经过符号链接')
        directory.mkdir(exist_ok=True)
    destination = directory / f'beeftv-{task_id}.mp4'
    client.download_resource(resource_id, destination)
    return {'path': destination.relative_to(outputs).as_posix(),
            'engine_revision': receipt['engine_revision']}
