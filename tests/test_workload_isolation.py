import os


def test_local_execution_keeps_arguments_and_environment_unchanged(monkeypatch):
    from easel.workload_isolation import worker_command, worker_environment
    monkeypatch.delenv('EASEL_WORKLOAD_ISOLATION', raising=False)
    command = ['/usr/bin/python3', '-c', 'print("stdio")', 'literal $(nothing)']
    assert worker_command(command, kind='agent') == command
    assert worker_environment({'HOME': '/fixture', 'MODEL_SECRET': 'fixture-only'}) == {'HOME': '/fixture', 'MODEL_SECRET': 'fixture-only'}


def test_linux_scopes_keep_the_executable_unmodified_and_separate_the_resource_slice(monkeypatch):
    import sys
    from easel.workload_isolation import worker_command, worker_environment, scope_unit
    monkeypatch.setenv('EASEL_WORKLOAD_ISOLATION', '1')
    monkeypatch.setattr(sys, 'platform', 'linux')
    command = ['/usr/bin/python3', '-c', 'print("stdio")', 'literal $(nothing)']
    wrapped = worker_command(command, kind='agent')
    assert wrapped[-len(command):] == command
    assert '--user' in wrapped and '--scope' in wrapped and '--quiet' in wrapped
    assert '--slice=chirpiva-workloads.slice' in wrapped
    assert scope_unit(wrapped).startswith('chirpiva-agent-')
    assert scope_unit(wrapped).endswith('.scope')
    env = worker_environment({'HOME': '/srv/chirpiva', 'MODEL_SECRET': 'fixture-only'})
    assert env['MODEL_SECRET'] == 'fixture-only'
    assert env['XDG_RUNTIME_DIR'] == f'/run/user/{os.getuid()}'
    assert env['DBUS_SESSION_BUS_ADDRESS'] == f'unix:path=/run/user/{os.getuid()}/bus'
