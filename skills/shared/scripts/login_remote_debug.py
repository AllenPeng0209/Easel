"""Private loopback bridge for one browser owned by a login subprocess.

Only login call sites opt into this wrapper. The descriptor stays in a host-only
0700 directory; neither it nor the CDP address is part of the public API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


ENVIRONMENT_KEY = 'EASEL_LOGIN_REMOTE_ENDPOINT_FILE'


def enabled() -> bool:
    return bool(os.environ.get(ENVIRONMENT_KEY, '').strip())


def _destination() -> Path:
    path = Path(os.environ.get(ENVIRONMENT_KEY, ''))
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('登录交互通道的存储位置无效')
    try:
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) & 0o077):
            raise ValueError('登录交互通道需要私有存储目录')
        if path.exists():
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or current.st_uid != os.getuid():
                raise ValueError('登录交互通道的存储位置无效')
    except OSError:
        raise ValueError('登录交互通道的存储位置无效') from None
    return path


def _fingerprint(path: Path):
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_size > 4096):
            raise ValueError('登录浏览器连接信息无效')
        return info.st_ino, info.st_mtime_ns, info.st_size, path.read_bytes()
    except FileNotFoundError:
        return None


def _fresh_endpoint(profile: Path, previous) -> str:
    active = _fingerprint(profile / 'DevToolsActivePort')
    if active is None or active == previous:
        raise RuntimeError('登录浏览器未创建新的交互通道')
    lines = active[-1].decode('ascii', errors='strict').splitlines()
    if (len(lines) != 2 or not lines[0].isdigit()
            or not 1024 <= int(lines[0]) <= 65535
            or not re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+', lines[1])):
        raise RuntimeError('登录浏览器连接信息无效')
    port = int(lines[0])
    endpoint = f'http://127.0.0.1:{port}'
    # Ignore ambient proxy variables: this request must stay on loopback.
    with build_opener(ProxyHandler({})).open(endpoint + '/json/version', timeout=3) as response:
        payload = json.loads(response.read(65536))
    websocket = urlsplit(payload.get('webSocketDebuggerUrl', ''))
    if (websocket.scheme != 'ws' or websocket.hostname != '127.0.0.1'
            or websocket.port != port or websocket.path != lines[1]
            or websocket.username or websocket.password or websocket.query or websocket.fragment):
        raise RuntimeError('登录浏览器连接归属无法核实')
    return endpoint


def _save(path: Path, payload: dict) -> bytes:
    content = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    fd, temporary = tempfile.mkstemp(prefix='.login-endpoint-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return content


def launch_persistent_context(chromium, profile: Path | str, **options):
    """Launch the requested login browser and publish only its fresh endpoint."""
    if not enabled():
        return chromium.launch_persistent_context(str(profile), **options)
    destination = _destination()
    destination.unlink(missing_ok=True)
    profile = Path(profile)
    previous = _fingerprint(profile / 'DevToolsActivePort')
    arguments = [argument for argument in options.get('args', ())
                 if not argument.startswith(('--remote-debugging-', '--remote-allow-origins'))]
    options = {**options, 'args': [*arguments, '--remote-debugging-port=0',
                                  '--remote-debugging-address=127.0.0.1']}
    context = chromium.launch_persistent_context(str(profile), **options)
    try:
        endpoint = _fresh_endpoint(profile, previous)
        payload = {'endpoint': endpoint, 'pid': os.getpid()}
        content = _save(destination, payload)
    except Exception:
        context.close()
        destination.unlink(missing_ok=True)
        raise RuntimeError('登录浏览器交互通道未能建立，请重新登录') from None
    descriptor = {'path': destination, 'payload': payload, 'content': content}
    context._easel_remote_login = descriptor
    original_close = context.close

    def close(*args, **kwargs):
        try:
            return original_close(*args, **kwargs)
        finally:
            # A newer runner must never lose its descriptor when this one exits.
            try:
                if not destination.is_symlink() and destination.read_bytes() == descriptor['content']:
                    destination.unlink()
            except OSError:
                pass

    context.close = close
    return context


def publish_page(context, page) -> None:
    """Bind the descriptor to the runner's main login page, never a probe tab."""
    descriptor = getattr(context, '_easel_remote_login', None)
    if descriptor is None:
        return
    if page.context is not context:
        raise ValueError('登录页面不属于当前浏览器')
    session = context.new_cdp_session(page)
    try:
        target = session.send('Target.getTargetInfo')['targetInfo']['targetId']
    finally:
        session.detach()
    if not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', target):
        raise RuntimeError('登录页面身份无法核实')
    descriptor['payload']['page_target_id'] = target
    descriptor['content'] = _save(descriptor['path'], descriptor['payload'])
