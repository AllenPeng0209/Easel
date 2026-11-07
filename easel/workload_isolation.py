"""Keep Linux execution children outside the latency-sensitive Web cgroup."""
from __future__ import annotations

import os
import re
import sys
import uuid

_UNIT = re.compile(r'chirpiva-(?:agent|catalog|video|media)-[a-f0-9]{32}\.scope')


def enabled():
    return sys.platform.startswith('linux') and os.environ.get('EASEL_WORKLOAD_ISOLATION') == '1'


def worker_command(command, *, kind='agent'):
    args = list(command)
    if not enabled():
        return args
    if kind not in {'agent', 'catalog', 'video', 'media'}:
        raise ValueError('Unsupported workload kind')
    unit = f'chirpiva-{kind}-{uuid.uuid4().hex}.scope'
    return ['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '--collect',
            '--slice=chirpiva-workloads.slice', '--unit=' + unit, '--', *args]


def worker_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    if enabled():
        runtime = f'/run/user/{os.getuid()}'
        result.setdefault('XDG_RUNTIME_DIR', runtime)
        result.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={runtime}/bus')
    return result


def scope_unit(command):
    for argument in command:
        if argument.startswith('--unit=') and _UNIT.fullmatch(argument[len('--unit='):]):
            return argument[len('--unit='):]
    return None
