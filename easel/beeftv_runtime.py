"""Per-IP writable BeefTV instances over one shared installation."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit
import uuid

VERSION = '2.1.1'
COMMIT = '60d22756918127106190d48587b9735c8156dbcf'
_LOCKS: dict[str, threading.RLock] = {}
_LOCK_GUARD = threading.Lock()
_DEPTH = threading.local()


class RuntimeConfigError(RuntimeError):
    """A local instance cannot safely be selected or operated."""


@dataclass(frozen=True)
class RuntimePaths:
    repository: Path
    workspace: Path
    project_id: str
    directory: Path
    data: Path
    run: Path
    logs: Path
    connection: Path
    instance: Path
    processes: Path
    shared: Path
    source: Path
    bin: Path
    cache: Path


def _safe(path, root):
    if path != root and not path.is_relative_to(root):
        raise RuntimeConfigError('创作服务目录不属于当前 IP')
    current = root
    for part in ('', *path.relative_to(root).parts):
        current = current / part
        if current.is_symlink():
            raise RuntimeConfigError('创作服务目录不能使用符号链接')


def _regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeConfigError('创作服务配置必须是普通私有文件')


def _read(path):
    _regular(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeConfigError('创作服务配置无法安全读取') from None
    with os.fdopen(descriptor, encoding='utf-8') as stream:
        try:
            body = stream.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise ValueError
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise RuntimeConfigError('创作服务配置损坏，请保留文件排查') from None


def runtime_paths(repository: Path, workspace: Path | None = None) -> RuntimePaths:
    repository = Path(repository).expanduser().absolute()
    if not repository.is_dir() or repository.is_symlink() or repository.resolve() != repository:
        raise RuntimeConfigError('Easel 仓库目录无效或含符号链接')
    selected = Path(workspace if workspace is not None else os.environ.get('EASEL_ROOT') or repository).expanduser().absolute()
    _safe(selected, repository)
    if not selected.is_dir() or selected.resolve() != selected:
        raise RuntimeConfigError('当前 IP 工作目录无效或含符号链接')
    project_id = 'default'
    if selected != repository:
        if selected.parent != repository / 'projects' or not re.fullmatch(r'[a-f0-9]{16}', selected.name):
            raise RuntimeConfigError('当前 IP 工作目录不属于本仓库')
        registry = _read(repository / 'projects/_registry.json')
        if not isinstance(registry, list) or not any(isinstance(item, dict) and item.get('id') == selected.name for item in registry):
            raise RuntimeConfigError('当前 IP 尚未登记')
        project_id = selected.name
    directory, shared = selected / '.easel/beeftv', repository / '.scratch/beeftv'
    _safe(directory, selected)
    _safe(shared, repository)
    return RuntimePaths(repository, selected, project_id, directory, directory / 'data', directory / 'run',
                        directory / 'logs', directory / 'run/client.json', directory / 'run/instance.json',
                        directory / 'run/processes.json', shared, shared / 'source', shared / 'bin', shared / 'cache')


def _mkdir(path, root):
    _safe(path, root)
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        current.mkdir(mode=0o700, exist_ok=True)
        if not current.is_dir() or current.is_symlink():
            raise RuntimeConfigError('创作服务目录无效')
        current.chmod(0o700)


def save_private_json(path: Path, value):
    _regular(path)
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeConfigError('创作服务配置目录尚未准备')
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        _regular(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _file_lock(path):
    _regular(path)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            if not os.fstat(descriptor).st_size:
                os.write(descriptor, b'0')
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == 'nt':
            import msvcrt
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def runtime_lock(paths: RuntimePaths):
    """One migration/lifecycle lock; nested operations in a thread reenter."""
    _mkdir(paths.run, paths.workspace)
    key = str(paths.run / 'runtime.lock')
    with _LOCK_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        depths = getattr(_DEPTH, 'values', None)
        if depths is None:
            depths = _DEPTH.values = {}
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
        else:
            with _file_lock(Path(key)):
                depths[key] = 1
                try:
                    yield
                finally:
                    depths.pop(key, None)


def _port(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or port is None or not 1024 <= port <= 65535):
            raise ValueError
        return port
    except (TypeError, ValueError):
        raise RuntimeConfigError('创作服务地址不是已绑定的本机端口') from None


def read_instance(paths: RuntimePaths) -> dict | None:
    _safe(paths.instance, paths.workspace)
    _safe(paths.data, paths.workspace)
    value = _read(paths.instance)
    if value is None:
        return None
    if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
            or value.get('workspace') != str(paths.workspace) or value.get('project_id') != paths.project_id
            or value.get('data_dir') != str(paths.data) or not isinstance(value.get('instance_id'), str)
            or not re.fullmatch(r'[a-f0-9]{32}', value['instance_id'])):
        raise RuntimeConfigError('创作服务配置不属于当前 IP')
    if _port(value.get('api_url')) == _port(value.get('ui_url')):
        raise RuntimeConfigError('创作服务端口配置冲突')
    return value


def ensure_instance(paths: RuntimePaths) -> dict:
    with runtime_lock(paths):
        for directory in (paths.data, paths.logs):
            _mkdir(directory, paths.workspace)
        registry_path = paths.shared / 'runtime-registry/instances.json'
        _mkdir(registry_path.parent, paths.repository)
        with _file_lock(registry_path.with_suffix('.lock')):
            registry = _read(registry_path)
            if registry is None:
                registry = {}
            if not isinstance(registry, dict):
                raise RuntimeConfigError('创作服务端口登记损坏，请保留文件排查')
            used = {7860, 8842, 8843}
            for item in registry.values():
                if not isinstance(item, dict):
                    raise RuntimeConfigError('创作服务端口登记损坏，请保留文件排查')
                used.update((_port(item.get('api_url')), _port(item.get('ui_url'))))
            value = read_instance(paths)
            if value is not None:
                own_ports = {_port(value['api_url']), _port(value['ui_url'])}
                if any(key != str(paths.workspace) and own_ports & {_port(item['api_url']), _port(item['ui_url'])} for key, item in registry.items()):
                    raise RuntimeConfigError('创作服务端口已归属其他 IP')
            else:
                sockets, ports = [], []
                try:
                    for _ in range(2):
                        for _attempt in range(100):
                            candidate = socket.socket()
                            candidate.bind(('127.0.0.1', 0))
                            port = candidate.getsockname()[1]
                            if port not in used:
                                sockets.append(candidate)
                                used.add(port)
                                ports.append(port)
                                break
                            candidate.close()
                        else:
                            raise RuntimeConfigError('没有可分配的本机创作服务端口')
                    value = {'version': 1, 'instance_id': uuid.uuid4().hex, 'workspace': str(paths.workspace),
                             'project_id': paths.project_id, 'data_dir': str(paths.data),
                             'api_url': f'http://127.0.0.1:{ports[0]}', 'ui_url': f'http://127.0.0.1:{ports[1]}'}
                    save_private_json(paths.instance, value)
                finally:
                    for candidate in sockets:
                        candidate.close()
            registry[str(paths.workspace)] = value
            save_private_json(registry_path, registry)
            return value


def read_connection(paths: RuntimePaths) -> dict | None:
    instance = read_instance(paths)
    _safe(paths.connection, paths.workspace)
    value = _read(paths.connection)
    if value is None:
        return None
    if (instance is None or not isinstance(value, dict)
            or any(value.get(key) != instance[key] for key in ('workspace', 'project_id', 'instance_id', 'data_dir', 'ui_url'))
            or value.get('base_url') != instance['api_url'] + '/api'
            or any(not isinstance(value.get(key), str) or not value[key].strip() or '\r' in value[key] or '\n' in value[key]
                   for key in ('client_id', 'client_token'))):
        raise RuntimeConfigError('创作服务连接不属于当前 IP')
    return value


def runtime_environment(paths: RuntimePaths, instance: dict, environ=None) -> dict:
    env = dict(os.environ if environ is None else environ)
    for key in ('DATABASE_URL', 'BEEFTV_BASE_URL', 'BEEFTV_CLIENT_ID', 'BEEFTV_CLIENT_TOKEN',
                'BEEFTV_OWNER_TOKEN', 'BEEFTV_DESKTOP_TOKEN', 'VITE_DESKTOP_LAUNCH_TOKEN', 'BEEFTV_AGENT_HOST_URL', 'BEEFTV_AGENT_HOST_TOKEN',
                'BEEFTV_AGENT_PORT', 'BEEFTV_OPS_URL'):
        env.pop(key, None)
    ui = instance['ui_url']
    localhost_ui = 'http://localhost:' + str(_port(ui))
    local_ffmpeg = paths.repository / '.scratch/media-bin/ffmpeg'
    ffmpeg = env.get('CANVAS_FFMPEG_PATH') or (str(local_ffmpeg) if local_ffmpeg.is_file() else shutil.which('ffmpeg')) or 'ffmpeg'
    env.update(EASEL_ROOT=str(paths.workspace), CANVAS_BACKEND_ADDR='127.0.0.1:' + str(_port(instance['api_url'])),
               CANVAS_BACKEND_DATA_DIR=str(paths.data), BEEFTV_DATA_DIR=str(paths.data),
               CANVAS_DATABASE_DRIVER='sqlite', CANVAS_AUTO_MIGRATE='true',
               CANVAS_CORS_ORIGINS=','.join([ui, localhost_ui, 'http://127.0.0.1:7860']),
               CANVAS_FFMPEG_PATH=ffmpeg, BEEFTV_UI_BOOTSTRAP='1', BEEFTV_ALLOWED_ORIGINS=ui + ',' + localhost_ui,
               BEEFTV_WORKSHOP_CLIENT_FILE=str(paths.connection), CANVAS_ALLOW_PRIVATE_UPSTREAMS='false',
               CANVAS_ALLOWED_PRIVATE_UPSTREAM_HOSTS='127.0.0.1', BEEFTV_FULL_MEDIA_RESOURCES='0',
               # An absolute loopback API selects BeefTV's SQLite-backed local
               # resource mode. Keep its URL on the UI origin so document
               # commits still pass through the narrow client-auth proxy.
               VITE_CANVAS_BACKEND_URL=ui + '/api',
               VITE_API_PROXY_TARGET=instance['api_url'], GIN_MODE='release')
    return env


def client_env(paths: RuntimePaths, environ=None) -> dict:
    instance, connection = read_instance(paths), read_connection(paths)
    if instance is None or connection is None:
        raise RuntimeConfigError('当前 IP 尚未连接独立创作服务')
    env = runtime_environment(paths, instance, environ)
    env.update(BEEFTV_BASE_URL=connection['base_url'], BEEFTV_CLIENT_ID=connection['client_id'], BEEFTV_CLIENT_TOKEN=connection['client_token'])
    return env


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _file, _code, _message, _headers, _new_url):
        return None


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def probe(url):
    try:
        with _opener().open(url, timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


def port_listening(url):
    try:
        with socket.create_connection(('127.0.0.1', _port(url)), timeout=.2):
            return True
    except OSError:
        return False


def owned_process(record):
    try:
        pid = record['pid']
        if type(pid) is not int or pid <= 0 or not record.get('markers'):
            return False
        os.kill(pid, 0)
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, check=True)
        arguments = shlex.split(result.stdout.strip())
        return all(marker in arguments for marker in record['markers'])
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def _markers(paths, instance, component):
    if component == 'backend':
        return [str(paths.bin / 'beeftv-server'), '--easel-instance=' + instance['instance_id']]
    if component == 'frontend':
        return [str(paths.source / 'web/node_modules/vite/bin/vite.js'), '--config',
                str(paths.repository / 'integrations/beeftv/vite.workshop.config.mts'), '--port', str(_port(instance['ui_url']))]
    raise RuntimeConfigError('未知创作服务组件')


def owned_runtime(paths: RuntimePaths, *, component='backend') -> bool:
    try:
        instance = read_instance(paths)
        _safe(paths.processes, paths.workspace)
        state = _read(paths.processes)
        if (instance is None or not isinstance(state, dict) or state.get('instance_id') != instance['instance_id']
                or state.get('workspace') != str(paths.workspace)):
            return False
        record = state.get(component)
        return isinstance(record, dict) and owned_process({**record, 'markers': _markers(paths, instance, component)})
    except (RuntimeConfigError, OSError):
        return False


def _process_preflight(paths, instance, state):
    if (not isinstance(state, dict) or state.get('instance_id') != instance['instance_id']
            or state.get('workspace') != str(paths.workspace)):
        raise RuntimeConfigError('创作服务进程记录不属于当前 IP')
    owned = {}
    for component, url in (('backend', instance['api_url']), ('frontend', instance['ui_url'])):
        record = state.get(component)
        owned[component] = owned_runtime(paths, component=component)
        if record is not None:
            if not isinstance(record, dict) or type(record.get('pid')) is not int or record['pid'] <= 0:
                raise RuntimeConfigError('创作服务进程记录损坏，请保留文件排查')
            alive = True
            try:
                os.kill(record['pid'], 0)
            except ProcessLookupError:
                alive = False
            except OSError:
                pass
            if alive and not owned[component]:
                raise RuntimeConfigError('创作服务活进程归属无法确认，进程记录已保留')
        if not owned[component] and port_listening(url):
            raise RuntimeConfigError('当前 IP 的创作服务端口被其他进程占用')
    return owned


def status(paths: RuntimePaths) -> dict:
    instance = read_instance(paths)
    result = {'running': False, 'project_id': paths.project_id, 'api_url': '', 'ui_url': '',
              'backend_live': False, 'backend_ready': False, 'frontend_ready': False, 'version': VERSION}
    if instance is None:
        return result
    backend, frontend = owned_runtime(paths), owned_runtime(paths, component='frontend')
    if backend:
        result.update(api_url=instance['api_url'], backend_live=probe(instance['api_url'] + '/api/health/live'),
                      backend_ready=probe(instance['api_url'] + '/api/health/ready'))
    if frontend:
        result['frontend_ready'] = probe(instance['ui_url'])
    result['running'] = bool(backend and frontend and result['backend_ready'] and result['frontend_ready'])
    if result['running']:
        result['ui_url'] = instance['ui_url']
    return result


def _request(paths, instance, path, *, method='GET', body=None, headers=None):
    if not owned_runtime(paths):
        raise RuntimeConfigError('当前 IP 创作服务进程归属无法确认')
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(instance['api_url'] + '/api' + path, data=payload, method=method,
                                     headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with _opener().open(request, timeout=15) as response:
            envelope = json.loads(response.read())
        if not isinstance(envelope, dict) or envelope.get('code') != 0:
            raise ValueError
        return envelope['data']
    except (OSError, ValueError, KeyError):
        raise RuntimeConfigError('当前 IP 创作服务未接受连接请求') from None


def connect(paths: RuntimePaths) -> dict:
    with runtime_lock(paths):
        instance = read_instance(paths)
        if instance is None or not owned_runtime(paths) or not probe(instance['api_url'] + '/api/health/ready'):
            raise RuntimeConfigError('请先启动当前 IP 的独立创作服务')
        existing = read_connection(paths)
        if existing is not None:
            try:
                _request(paths, instance, '/ops', headers={'X-Beeftv-Client': existing['client_id'], 'Authorization': 'Bearer ' + existing['client_token']})
            except RuntimeConfigError:
                existing = None
        if existing is None:
            owner_file = paths.data / 'agent_owner_token'
            _safe(owner_file, paths.workspace)
            _regular(owner_file)
            if not owner_file.is_file():
                raise RuntimeConfigError('当前 IP 创作服务尚未建立拥有者凭据')
            data = _request(paths, instance, '/agent-clients', method='POST',
                            body={'kind': 'other', 'label': 'Easel 视频工坊 · ' + paths.project_id, 'mode': 'read-write'},
                            headers={'X-Beeftv-Owner': owner_file.read_text(encoding='utf-8').strip()})
            try:
                value = {key: instance[key] for key in ('workspace', 'project_id', 'instance_id', 'data_dir', 'ui_url')}
                value.update(base_url=instance['api_url'] + '/api', client_id=data['client']['id'], client_token=data['token'])
            except (TypeError, KeyError):
                raise RuntimeConfigError('当前 IP 创作服务返回了无效连接') from None
            save_private_json(paths.connection, value)
            read_connection(paths)
        save_private_json(paths.run / 'mcp-config.json', {'mcpServers': {'beeftv': {'command': '/bin/bash',
                          'args': [str(paths.repository / 'scripts/beeftv_runtime.sh'), 'mcp'], 'env': {'EASEL_ROOT': str(paths.workspace)}}}})
        return {'connected': True, 'project_id': paths.project_id, 'credentials_file': str(paths.connection), 'mcp_config_file': str(paths.run / 'mcp-config.json')}


def _connect_runtime(paths):
    return connect(paths)


def ensure_started(paths: RuntimePaths, connect=True) -> dict:
    with runtime_lock(paths):
        instance = ensure_instance(paths)
        state = _read(paths.processes)
        if state is None:
            state = {'instance_id': instance['instance_id'], 'workspace': str(paths.workspace)}
        _process_preflight(paths, instance, state)
        node = shutil.which('node')
        backend, vite = paths.bin / 'beeftv-server', paths.source / 'web/node_modules/vite/bin/vite.js'
        config = paths.repository / 'integrations/beeftv/vite.workshop.config.mts'
        if not backend.is_file() or not vite.is_file() or not config.is_file() or not node:
            raise RuntimeConfigError('共享创作程序尚未安装，请先安装 BeefTV 运行环境')
        definitions = {'backend': ([str(backend), '--easel-instance=' + instance['instance_id']], paths.source / 'backend'),
                       'frontend': ([node, str(vite), '--config', str(config), '--host', '127.0.0.1', '--port', str(_port(instance['ui_url'])), '--strictPort'], paths.source / 'web')}
        for component, (command, cwd) in definitions.items():
            if owned_runtime(paths, component=component):
                continue
            with (paths.logs / (component + '.log')).open('ab', buffering=0) as log:
                process = subprocess.Popen(command, cwd=cwd, env=runtime_environment(paths, instance), stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            state[component] = {'pid': process.pid}
            save_private_json(paths.processes, state)
        for _ in range(40):
            result = status(paths)
            if result['running']:
                if connect:
                    _connect_runtime(paths)
                return result
            time.sleep(.5)
        raise RuntimeConfigError('当前 IP 创作服务启动未完成，请查看该 IP 的服务日志')


def stop(paths: RuntimePaths) -> dict:
    with runtime_lock(paths):
        instance = read_instance(paths)
        if instance is None:
            return {'stopped': True, 'project_id': paths.project_id}
        state = _read(paths.processes)
        if state is None:
            state = {'instance_id': instance['instance_id'], 'workspace': str(paths.workspace)}
        owned = _process_preflight(paths, instance, state)
        pending = []
        for component in ('frontend', 'backend'):
            if owned[component]:
                os.killpg(state[component]['pid'], signal.SIGTERM)
                pending.append(component)
        deadline = time.monotonic() + 20
        while any(owned_runtime(paths, component=name) for name in pending):
            if time.monotonic() >= deadline:
                raise RuntimeConfigError('当前 IP 创作服务仍在停止，进程记录已保留')
            time.sleep(.2)
        if any(port_listening(instance[key]) for key in ('api_url', 'ui_url')):
            raise RuntimeConfigError('当前 IP 的创作服务端口仍被占用，进程记录已保留')
        paths.processes.unlink(missing_ok=True)
        return {'stopped': True, 'project_id': paths.project_id}


def install(paths: RuntimePaths) -> dict:
    for command in ('git', 'go', 'bun', 'node'):
        if shutil.which(command) is None:
            raise RuntimeConfigError('缺少共享创作程序安装工具：' + command)
    for relative in ('bin/cli', 'cache/go-build', 'cache/go-mod', 'cache/bun', 'run'):
        _mkdir(paths.shared / relative, paths.repository)
    if not paths.source.exists():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'v' + VERSION, 'https://github.com/glanderness/BeefTV.git', str(paths.source)], check=True)
    if not (paths.source / '.git').is_dir():
        raise RuntimeConfigError('共享创作源码版本无法核实，已有目录保持不变')
    observed = subprocess.check_output(['git', '-C', str(paths.source), 'rev-parse', 'HEAD'], text=True).strip()
    if observed != COMMIT or not (paths.source / 'LICENSE').is_file():
        raise RuntimeConfigError('共享创作源码版本或许可证不匹配，已有目录保持不变')
    env = dict(os.environ, GOPATH=str(paths.cache / 'go'), GOMODCACHE=str(paths.cache / 'go-mod'), GOCACHE=str(paths.cache / 'go-build'), BUN_INSTALL_CACHE_DIR=str(paths.cache / 'bun'))
    flags = '-X infinite-canvas/backend/internal/buildinfo.Version=' + VERSION + ' -X infinite-canvas/backend/internal/buildinfo.Commit=' + COMMIT
    for executable, package in (('beeftv-server', './cmd/server'), ('cli/beeftv', './cmd/beeftv')):
        subprocess.run(['go', 'build', '-ldflags', flags, '-o', str(paths.bin / executable), package], cwd=paths.source / 'backend', env=env, check=True)
    subprocess.run(['bun', 'install', '--frozen-lockfile', '--ignore-scripts'], cwd=paths.source / 'web', env=env, check=True)
    save_private_json(paths.shared / 'run/installation.json', {'version': VERSION, 'commit': COMMIT, 'source': str(paths.source), 'license': str(paths.source / 'LICENSE'), 'model_downloads': False})
    return {'installed': True, 'version': VERSION, 'commit': COMMIT, 'source': str(paths.source)}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != '--repository':
        raise RuntimeConfigError('请通过 Easel 创作服务脚本选择仓库')
    repository, args = Path(args[1]), args[2:]
    action = args.pop(0) if args else 'status'
    paths = runtime_paths(repository)
    if action in {'cli', 'mcp'}:
        if not owned_runtime(paths):
            raise RuntimeConfigError('当前 IP 的独立创作服务尚未启动')
        command = [str(paths.bin / 'cli/beeftv'), *(['mcp', 'serve'] if action == 'mcp' else []), *args]
        return subprocess.call(command, env=client_env(paths))
    actions = {'status': status, 'start': ensure_started, 'connect': connect, 'stop': stop, 'install': install, 'bootstrap': install}
    if action not in actions or args:
        raise RuntimeConfigError('用法：beeftv_runtime.sh install|start|connect|stop|status|cli ...|mcp')
    print(json.dumps(actions[action](paths), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeConfigError, OSError, subprocess.SubprocessError):
        print('当前 IP 创作服务操作未完成，请检查该 IP 的运行配置和日志。', file=sys.stderr)
        raise SystemExit(1) from None
