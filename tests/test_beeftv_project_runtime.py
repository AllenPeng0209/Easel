"""Each IP must select an owned engine, not merely a different canvas."""
from copy import deepcopy
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
from types import SimpleNamespace

import pytest


def runtime():
    name = 'easel.beeftv_runtime'
    assert importlib.util.find_spec(name) is not None, 'BeefTV has no per-IP runtime launcher'
    return importlib.import_module(name)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.delenv('EASEL_ROOT', raising=False)
    (tmp_path / 'projects').mkdir()
    identifiers = ['a' * 16, 'b' * 16]
    (tmp_path / 'projects/_registry.json').write_text(json.dumps([{'id': 'default'}, *[{'id': value} for value in identifiers]]))
    for identifier in identifiers:
        (tmp_path / 'projects' / identifier).mkdir()
    return tmp_path


def connection(module, paths, instance, secret='fixture-key'):
    value = {'workspace': str(paths.workspace), 'project_id': instance['project_id'],
             'instance_id': instance['instance_id'], 'data_dir': str(paths.data),
             'base_url': instance['api_url'] + '/api', 'ui_url': instance['ui_url'],
             'client_id': 'fixture-client', 'client_token': secret}
    module.save_private_json(paths.connection, value)
    return value


def test_default_and_two_project_instances_keep_distinct_owned_data_and_ports(repository):
    module = runtime()
    paths = [module.runtime_paths(repository, workspace) for workspace in
             [repository, repository / 'projects' / ('a' * 16), repository / 'projects' / ('b' * 16)]]
    instances = [module.ensure_instance(path) for path in paths]
    assert len({item['api_url'] for item in instances} | {item['ui_url'] for item in instances}) == 6
    assert [item['project_id'] for item in instances] == ['default', 'a' * 16, 'b' * 16]
    for path, item in zip(paths, instances):
        assert path.directory == path.workspace / '.easel/beeftv'
        assert item['data_dir'] == str(path.workspace / '.easel/beeftv/data')
        assert path.source == repository / '.scratch/beeftv/source'
        assert module.ensure_instance(path) == item
        assert stat.S_IMODE(path.instance.stat().st_mode) == 0o600
    assert not (repository / '.scratch/beeftv/data').exists()


def test_status_of_an_unconfigured_ip_does_not_start_or_fall_back_to_legacy(repository, monkeypatch):
    module = runtime()
    legacy = repository / '.scratch/beeftv/run'
    legacy.mkdir(parents=True)
    (legacy / 'client.json').write_text(json.dumps({'client_token': 'legacy-private-value'}))
    monkeypatch.setattr(module, 'probe', lambda *_: pytest.fail('Unowned legacy service was probed'))
    paths = module.runtime_paths(repository)
    result = module.status(paths)
    assert result['running'] is False
    assert result.get('ui_url', '') == ''
    assert 'legacy-private-value' not in json.dumps(result)
    assert not paths.directory.exists()


def test_explicit_workspace_and_easel_root_are_validated_before_creating_state(repository, tmp_path, monkeypatch):
    module = runtime()
    selected = repository / 'projects' / ('a' * 16)
    monkeypatch.setenv('EASEL_ROOT', str(selected))
    assert module.runtime_paths(repository).workspace == selected
    with pytest.raises((ValueError, module.RuntimeConfigError)):
        module.runtime_paths(repository, tmp_path.parent)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (repository / 'projects' / ('b' * 16)).rmdir()
    (repository / 'projects' / ('b' * 16)).symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, module.RuntimeConfigError)):
        module.runtime_paths(repository, repository / 'projects' / ('b' * 16))
    assert not (outside / '.easel').exists()


def test_environment_cannot_override_owned_data_or_reuse_another_client(repository):
    module = runtime()
    paths = module.runtime_paths(repository)
    instance = module.ensure_instance(paths)
    old = {'DATABASE_URL': 'sqlite:/shared.sqlite', 'BEEFTV_CLIENT_TOKEN': 'wrong-token',
           'BEEFTV_CLIENT_ID': 'wrong-client', 'BEEFTV_BASE_URL': 'http://127.0.0.1:8842/api',
           'BEEFTV_OWNER_TOKEN': 'wrong-owner', 'BEEFTV_DESKTOP_TOKEN': 'wrong-desktop',
           'VITE_DESKTOP_LAUNCH_TOKEN': 'wrong-vite-launch',
           'CANVAS_BACKEND_DATA_DIR': '/shared', 'PATH': os.environ['PATH'],
           'VITE_CANVAS_BACKEND_URL': 'http://127.0.0.1:8843/api'}
    env = module.runtime_environment(paths, instance, old)
    assert 'DATABASE_URL' not in env
    assert all(key not in env for key in ['BEEFTV_CLIENT_TOKEN', 'BEEFTV_CLIENT_ID', 'BEEFTV_OWNER_TOKEN', 'BEEFTV_DESKTOP_TOKEN'])
    assert 'VITE_DESKTOP_LAUNCH_TOKEN' not in env
    assert env['CANVAS_BACKEND_DATA_DIR'] == str(paths.data)
    assert env['CANVAS_BACKEND_ADDR'] == instance['api_url'].removeprefix('http://')
    assert env['VITE_API_PROXY_TARGET'] == instance['api_url']
    assert env['VITE_CANVAS_BACKEND_URL'] == instance['ui_url'] + '/api'
    assert env['BEEFTV_WORKSHOP_CLIENT_FILE'] == str(paths.connection)
    assert instance['ui_url'] in env['CANVAS_CORS_ORIGINS']
    assert instance['ui_url'] in env['BEEFTV_ALLOWED_ORIGINS']


def test_foreign_connection_and_descriptor_are_rejected_without_rewriting(repository):
    module = runtime()
    first = module.runtime_paths(repository)
    second = module.runtime_paths(repository, repository / 'projects' / ('a' * 16))
    item = module.ensure_instance(first)
    module.ensure_instance(second)
    original = connection(module, first, item)
    assert module.read_connection(first)['client_id'] == 'fixture-client'
    module.save_private_json(second.connection, original)
    with pytest.raises(module.RuntimeConfigError):
        module.read_connection(second)
    foreign = deepcopy(item)
    foreign['data_dir'] = str(repository / '.scratch/beeftv/data')
    module.save_private_json(first.instance, foreign)
    before = first.instance.read_bytes()
    with pytest.raises(module.RuntimeConfigError):
        module.ensure_instance(first)
    assert first.instance.read_bytes() == before


def test_symlinked_runtime_files_are_never_used(repository):
    module = runtime()
    paths = module.runtime_paths(repository)
    item = module.ensure_instance(paths)
    original = connection(module, paths, item)
    elsewhere = repository / 'outside.json'
    elsewhere.write_text(json.dumps(original))
    paths.connection.unlink()
    paths.connection.symlink_to(elsewhere)
    with pytest.raises(module.RuntimeConfigError):
        module.read_connection(paths)


def test_occupied_unowned_port_is_not_accepted_as_a_running_runtime(repository, monkeypatch):
    module = runtime()
    paths = module.runtime_paths(repository)
    item = module.ensure_instance(paths)
    port = int(item['api_url'].rsplit(':', 1)[1])
    listener = socket.socket()
    listener.bind(('127.0.0.1', port))
    listener.listen(1)
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Foreign port must not be replaced'))
    try:
        with pytest.raises(module.RuntimeConfigError, match='端口'):
            module.ensure_started(paths)
        assert module.status(paths)['running'] is False
    finally:
        listener.close()


def test_start_records_marker_bound_processes_and_scoped_environment(repository, monkeypatch):
    module = runtime()
    paths = module.runtime_paths(repository)
    (paths.bin).mkdir(parents=True)
    (paths.bin / 'beeftv-server').write_text('fixture')
    vite = paths.source / 'web/node_modules/vite/bin/vite.js'
    vite.parent.mkdir(parents=True)
    vite.write_text('fixture')
    config = repository / 'integrations/beeftv/vite.workshop.config.mts'
    config.parent.mkdir(parents=True)
    config.write_text('fixture')
    spawned = []
    def spawn(command, **kwargs):
        spawned.append((command, kwargs))
        return SimpleNamespace(pid=12300 + len(spawned))
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    monkeypatch.setattr(module, 'owned_process', lambda record: record.get('pid', 0) in {12301, 12302})
    monkeypatch.setattr(module, 'probe', lambda *_: True)
    monkeypatch.setattr(module, 'port_listening', lambda *_: False)
    monkeypatch.setattr(module, 'connect', lambda _paths: {'connected': True})
    result = module.ensure_started(paths)
    assert result['running'] is True
    assert len(spawned) == 2
    item = module.read_instance(paths)
    assert '--easel-instance=' + item['instance_id'] in spawned[0][0]
    assert spawned[1][0][-3:] == ['--port', item['ui_url'].rsplit(':', 1)[1], '--strictPort']
    assert all(kwargs['env']['CANVAS_BACKEND_DATA_DIR'] == str(paths.data) for _, kwargs in spawned)
    assert module.status(paths)['running'] is True


def test_cli_status_uses_project_workspace_without_creating_a_global_runtime(repository):
    module = runtime()
    source = Path(module.__file__)
    destination = repository / 'easel/beeftv_runtime.py'
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    wrapper = repository / 'scripts/beeftv_runtime.sh'
    wrapper.parent.mkdir()
    wrapper.write_bytes((source.parents[1] / 'scripts/beeftv_runtime.sh').read_bytes())
    selected = repository / 'projects' / ('a' * 16)
    result = subprocess.run(['bash', str(wrapper), 'status'], env={**os.environ, 'EASEL_ROOT': str(selected), 'BEEFTV_CLIENT_TOKEN': 'must-not-print'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['project_id'] == 'a' * 16
    assert data['running'] is False
    assert 'must-not-print' not in result.stdout + result.stderr
    assert not (repository / '.scratch/beeftv/data').exists()
    assert not (selected / '.easel').exists()


@pytest.mark.parametrize('field,value', [('version', True), ('api_url', True), ('ui_url', None)])
def test_malformed_descriptor_types_fail_closed(repository, field, value):
    module = runtime()
    paths = module.runtime_paths(repository)
    instance = module.ensure_instance(paths)
    instance[field] = value
    module.save_private_json(paths.instance, instance)
    with pytest.raises(module.RuntimeConfigError):
        module.read_instance(paths)


def test_a_corrupt_empty_registry_is_not_silently_replaced(repository):
    module = runtime()
    paths = module.runtime_paths(repository)
    module.ensure_instance(paths)
    registry = paths.shared / 'runtime-registry/instances.json'
    registry.write_text('[]')
    with pytest.raises(module.RuntimeConfigError):
        module.ensure_instance(paths)
    assert registry.read_text() == '[]'


def test_a_runtime_data_symlink_cannot_redirect_an_owned_descriptor_to_shared_data(repository):
    module = runtime()
    paths = module.runtime_paths(repository)
    instance = module.ensure_instance(paths)
    connection(module, paths, instance)
    elsewhere = repository / 'outside-data'
    elsewhere.mkdir()
    paths.data.rmdir()
    paths.data.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(module.RuntimeConfigError):
        module.read_connection(paths)


def test_process_ownership_uses_expected_instance_not_markers_supplied_in_the_record(repository, monkeypatch):
    module = runtime()
    paths = module.runtime_paths(repository)
    instance = module.ensure_instance(paths)
    module.save_private_json(paths.processes, {'instance_id': instance['instance_id'], 'workspace': str(paths.workspace),
                              'backend': {'pid': 12345, 'markers': ['foreign-server']}})
    monkeypatch.setattr(module.os, 'kill', lambda *_: None)
    monkeypatch.setattr(module.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(stdout='foreign-server --easel-instance=other'))
    assert module.owned_runtime(paths) is False
    monkeypatch.setattr(module.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(
        stdout=str(paths.bin / 'beeftv-server') + ' --easel-instance=' + instance['instance_id']))
    assert module.owned_runtime(paths) is True


def test_runtime_lock_reenters_when_start_needs_an_instance(repository):
    module = runtime()
    paths = module.runtime_paths(repository)
    with module.runtime_lock(paths):
        first = module.ensure_instance(paths)
        with module.runtime_lock(paths):
            assert module.ensure_instance(paths) == first


@pytest.mark.parametrize('operation', ['stop', 'ensure_started'])
def test_an_unknown_live_pid_is_preserved_and_not_claimed_offline(repository, operation, monkeypatch):
    module = runtime()
    paths = module.runtime_paths(repository)
    instance = module.ensure_instance(paths)
    module.save_private_json(paths.processes, {'instance_id': instance['instance_id'], 'workspace': str(paths.workspace),
                              'backend': {'pid': os.getpid()}})
    before = paths.processes.read_bytes()
    monkeypatch.setattr(module, 'owned_runtime', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(module, 'port_listening', lambda *_: False)
    monkeypatch.setattr(module.os, 'killpg', lambda *_: pytest.fail('An unknown process must not be killed'))
    with pytest.raises(module.RuntimeConfigError, match='归属'):
        getattr(module, operation)(paths)
    assert paths.processes.read_bytes() == before


def test_stop_checks_the_instance_after_acquiring_the_lifecycle_lock(repository, monkeypatch):
    module = runtime()
    paths = module.runtime_paths(repository)
    original_lock = module.runtime_lock
    first = True

    @contextmanager
    def racing_start(chosen):
        nonlocal first
        with original_lock(chosen):
            if first:
                first = False
                with monkeypatch.context() as patch:
                    patch.setattr(module, 'runtime_lock', original_lock)
                    instance = module.ensure_instance(chosen)
                module.save_private_json(chosen.processes, {'instance_id': instance['instance_id'],
                    'workspace': str(chosen.workspace), 'backend': {'pid': os.getpid()}})
            yield

    monkeypatch.setattr(module, 'runtime_lock', racing_start)
    monkeypatch.setattr(module, 'owned_runtime', lambda *_args, **_kwargs: False)
    monkeypatch.setattr(module, 'port_listening', lambda *_: False)
    with pytest.raises(module.RuntimeConfigError, match='归属'):
        module.stop(paths)
    assert paths.processes.is_file()
