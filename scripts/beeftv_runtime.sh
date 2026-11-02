#!/usr/bin/env bash
# Local, isolated BeefTV runtime used by Easel's video workshop.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# Keep stdin available for the MCP JSON-RPC transport.
exec python3 /dev/fd/3 "$project_root" "$@" 3<<'PY'
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

project = Path(sys.argv[1])
root = project / '.scratch' / 'beeftv'
source = root / 'source'
action = sys.argv[2] if len(sys.argv) > 2 else 'status'
state_path = root / 'run' / 'processes.json'
api = 'http://127.0.0.1:8842'
ui = 'http://127.0.0.1:8843'
version = '2.1.1'
commit = '60d22756918127106190d48587b9735c8156dbcf'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def probe(url):
    try:
        with opener.open(url, timeout=3) as response:
            return response.status == 200
    except OSError:
        return False

def read_state():
    return json.loads(state_path.read_text()) if state_path.is_file() else {}

def owned_process(record):
    try:
        pid = int(record['pid'])
        os.kill(pid, 0)
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, check=True)
        return record['identity'] in result.stdout
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False

def client_env():
    env = dict(os.environ, BEEFTV_BASE_URL=api + '/api', BEEFTV_DATA_DIR=str(root / 'data'))
    env.pop('BEEFTV_OWNER_TOKEN', None)
    env.pop('BEEFTV_DESKTOP_TOKEN', None)
    credentials = root / 'run' / 'client.json'
    if credentials.is_file():
        data = json.loads(credentials.read_text())
        env.update(BEEFTV_CLIENT_ID=data['client_id'], BEEFTV_CLIENT_TOKEN=data['client_token'])
    return env

def save_private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def request_json(path, *, method='GET', body=None, headers=None):
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(api + '/api' + path, data=payload, method=method,
        headers={'Content-Type': 'application/json', **(headers or {})})
    with opener.open(request, timeout=15) as response:
        envelope = json.loads(response.read())
    if envelope.get('code') != 0:
        raise RuntimeError('BeefTV rejected the local request: ' + str(envelope.get('reason', envelope.get('code'))))
    return envelope['data']

def install():
    for command in ('git', 'go', 'bun', 'node'):
        if shutil.which(command) is None:
            raise SystemExit('Missing prerequisite: ' + command)
    for directory in ('bin/cli', 'cache/go-build', 'cache/go-mod', 'cache/bun', 'run', 'logs', 'data'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    if not source.exists():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'v' + version,
            'https://github.com/glanderness/BeefTV.git', str(source)], check=True)
    if not (source / '.git').is_dir():
        raise SystemExit('BeefTV source revision cannot be verified; existing directory is preserved')
    observed = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if observed != commit:
        raise SystemExit('BeefTV source revision mismatch; refusing to replace the existing checkout')
    if not (source / 'LICENSE').is_file():
        raise SystemExit('Pinned BeefTV source has no LICENSE; installation stopped')
    env = dict(os.environ, GOPATH=str(root / 'cache' / 'go'), GOMODCACHE=str(root / 'cache' / 'go-mod'),
        GOCACHE=str(root / 'cache' / 'go-build'), BUN_INSTALL_CACHE_DIR=str(root / 'cache' / 'bun'))
    flags = '-X infinite-canvas/backend/internal/buildinfo.Version=' + version + \
        ' -X infinite-canvas/backend/internal/buildinfo.Commit=' + commit
    for executable, package in (('beeftv-server', './cmd/server'), ('cli/beeftv', './cmd/beeftv')):
        subprocess.run(['go', 'build', '-ldflags', flags, '-o', str(root / 'bin' / executable), package],
            cwd=source / 'backend', env=env, check=True)
    subprocess.run(['bun', 'install', '--frozen-lockfile', '--ignore-scripts'], cwd=source / 'web', env=env, check=True)
    save_private_json(root / 'run' / 'installation.json', {'version': version, 'commit': commit,
        'source': str(source), 'license': str(source / 'LICENSE'), 'model_downloads': False})
    print(json.dumps({'installed': True, 'version': version, 'commit': commit, 'source': str(source)}))

def connect():
    if not probe(api + '/api/health/ready'):
        raise SystemExit('Start the local BeefTV runtime before connecting a client')
    path = root / 'run' / 'client.json'
    existing = None
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
            request_json('/ops', headers={'X-Beeftv-Client': existing['client_id'],
                'Authorization': 'Bearer ' + existing['client_token']})
        except (OSError, ValueError, KeyError, RuntimeError):
            existing = None
    if existing is None:
        owner_file = root / 'data' / 'agent_owner_token'
        if not owner_file.is_file():
            raise SystemExit('The local runtime has not created its owner credential')
        data = request_json('/agent-clients', method='POST',
            body={'kind': 'other', 'label': 'Easel 视频工坊', 'mode': 'read-write'},
            headers={'X-Beeftv-Owner': owner_file.read_text().strip()})
        save_private_json(path, {'client_id': data['client']['id'], 'client_token': data['token'],
            'base_url': api + '/api', 'data_dir': str(root / 'data')})
    else:
        os.chmod(path, 0o600)
    save_private_json(root / 'run' / 'mcp-config.json', {'mcpServers': {'beeftv': {
        'command': '/bin/bash', 'args': [str(project / 'scripts' / 'beeftv_runtime.sh'), 'mcp']}}})
    print(json.dumps({'connected': True, 'credentials_file': str(path),
        'mcp_config_file': str(root / 'run' / 'mcp-config.json'),
        'model_setup': {'script': str(project / 'integrations' / 'beeftv' / 'configure_h3.py'),
                        'modes': ['ref11', 't2va'], 'automatic': False}}))

if action in ('cli', 'mcp'):
    cli = root / 'bin' / 'cli' / 'beeftv'
    args = sys.argv[3:] if action == 'cli' else ['mcp', 'serve', *sys.argv[3:]]
    raise SystemExit(subprocess.call([str(cli), *args], env=client_env()))

if action in ('install', 'bootstrap'):
    install()
    raise SystemExit(0)
if action == 'connect':
    connect()
    raise SystemExit(0)

if action == 'start':
    for directory in ('logs', 'run', 'data'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    backend = root / 'bin' / 'beeftv-server'
    vite = source / 'web' / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    vite_config = project / 'integrations' / 'beeftv' / 'vite.workshop.config.mts'
    node = shutil.which('node')
    if not backend.is_file() or not vite.is_file() or not vite_config.is_file() or not node:
        raise SystemExit('BeefTV runtime is not installed; see docs/beeftv-video-workshop.md')
    env = dict(os.environ)
    local_ffmpeg = project / '.scratch' / 'media-bin' / 'ffmpeg'
    ffmpeg = os.environ.get('CANVAS_FFMPEG_PATH') or (str(local_ffmpeg) if local_ffmpeg.is_file() else shutil.which('ffmpeg')) or 'ffmpeg'
    env.update(CANVAS_BACKEND_ADDR='127.0.0.1:8842', CANVAS_BACKEND_DATA_DIR=str(root / 'data'),
               CANVAS_DATABASE_DRIVER='sqlite', CANVAS_AUTO_MIGRATE='true',
               CANVAS_CORS_ORIGINS=ui + ',http://localhost:8843,http://127.0.0.1:7860',
               CANVAS_FFMPEG_PATH=ffmpeg, BEEFTV_UI_BOOTSTRAP='1',
               BEEFTV_ALLOWED_ORIGINS=ui + ',http://localhost:8843',
               BEEFTV_WORKSHOP_CLIENT_FILE=str(root / 'run' / 'client.json'),
               CANVAS_ALLOW_PRIVATE_UPSTREAMS='false', CANVAS_ALLOWED_PRIVATE_UPSTREAM_HOSTS='127.0.0.1',
               BEEFTV_FULL_MEDIA_RESOURCES='0', VITE_API_PROXY_TARGET=api, GIN_MODE='release')
    definitions = {
        'backend': ([str(backend)], source / 'backend', api + '/api/health/live', str(backend)),
        'frontend': ([node, str(vite), '--config', str(vite_config), '--host', '127.0.0.1', '--port', '8843', '--strictPort'], source / 'web', ui, str(vite)),
    }
    state = read_state()
    for name, (command, cwd, url, identity) in definitions.items():
        if probe(url):
            continue
        if owned_process(state.get(name, {})):
            continue
        log = (root / 'logs' / (name + '.log')).open('ab', buffering=0)
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            log.close()
        state[name] = {'pid': process.pid, 'identity': identity}
        state_path.write_text(json.dumps(state, indent=2) + '\n')
    for _ in range(40):
        if all(probe(item[2]) for item in definitions.values()):
            print(json.dumps({'running': True, 'api_url': api, 'ui_url': ui, 'version': '2.1.1'}))
            break
        time.sleep(.5)
    else:
        raise SystemExit('BeefTV startup incomplete; inspect .scratch/beeftv/logs/')
elif action == 'stop':
    state = read_state()
    stopping = []
    for name in ('frontend', 'backend'):
        record = state.get(name, {})
        if owned_process(record):
            os.killpg(int(record['pid']), signal.SIGTERM)
            stopping.append(record)
            print('Stopping BeefTV ' + name)
    deadline = time.monotonic() + 20
    while any(owned_process(record) for record in stopping):
        if time.monotonic() >= deadline:
            raise SystemExit('BeefTV is still stopping; process records retained')
        time.sleep(.2)
    state_path.unlink(missing_ok=True)
elif action == 'status':
    print(json.dumps({'api_url': api, 'ui_url': ui,
                      'backend_live': probe(api + '/api/health/live'),
                      'backend_ready': probe(api + '/api/health/ready'),
                      'frontend_ready': probe(ui), 'version': '2.1.1'}))
else:
    raise SystemExit('Usage: scripts/beeftv_runtime.sh install|bootstrap|start|connect|stop|status|cli ...|mcp')
PY
