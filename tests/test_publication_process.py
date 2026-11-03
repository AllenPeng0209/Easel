"""Local executable fixtures only: no browser, platform, model or network."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


def api():
    return importlib.import_module('easel.publication_process')


async def wait_file(path, timeout=4):
    deadline = asyncio.get_running_loop().time() + timeout
    while not path.exists():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f'local fixture did not create {path.name}')
        await asyncio.sleep(.01)


def process_tree(tmp_path, *, root_exits=False):
    """Three actual generations; every running leaf appends a heartbeat."""
    leaf = ("import os,time,signal,pathlib; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "pathlib.Path('leaf.pid').write_text(str(os.getpid())); "
            "p=pathlib.Path('heartbeat'); "
            "\nwhile True:\n with p.open('a') as f: f.write('x')\n time.sleep(.03)\n")
    child = ("import os,time,signal,pathlib,subprocess,sys; "
             "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             f"subprocess.Popen([sys.executable,'-c',{leaf!r}]); "
             "pathlib.Path('child.pid').write_text(str(os.getpid())); time.sleep(60)")
    root = ("import os,time,signal,pathlib,subprocess,sys; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
            "pathlib.Path('root.pid').write_text(str(os.getpid())); "
            + ("time.sleep(.15)" if root_exits else "time.sleep(60)"))
    path = tmp_path / 'tree.py'
    path.write_text(root)
    return [sys.executable, str(path)]


def running(pid):
    if os.name == 'nt':
        rows = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                              capture_output=True, text=True, timeout=5).stdout
        return f'"{pid}"' in rows
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True, timeout=5)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


def emergency_cleanup(tmp_path):
    path = tmp_path / 'root.pid'
    if path.exists() and os.name != 'nt':
        try:
            os.killpg(int(path.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


async def assert_tree_stopped(tmp_path):
    for name in ('root.pid', 'child.pid', 'leaf.pid'):
        assert not running(int((tmp_path / name).read_text())), f'{name} still executing'
    before = (tmp_path / 'heartbeat').read_text()
    await asyncio.sleep(.15)
    assert (tmp_path / 'heartbeat').read_text() == before


def test_fixed_argv_env_cwd_and_exit_code_are_returned_without_success_inference(tmp_path):
    async def scenario():
        script = "import os,json,sys; print(json.dumps([os.getcwd(),os.environ.get('FIXTURE_VALUE'),sys.argv[1]])); sys.exit(7)"
        argv = [sys.executable, '-c', script, 'literal; $(touch never)']
        result = await api().run_publication_process(argv, cwd=tmp_path, env={'FIXTURE_VALUE': 'local'}, timeout=3)
        assert result.returncode == 7 and result.started is True and result.timed_out is False
        assert json.loads(result.stdout) == [str(tmp_path), 'local', 'literal; $(touch never)']
        assert result.args == tuple(argv) and result.cleanup_confirmed is True
        assert not (tmp_path / 'never').exists()
    asyncio.run(scenario())


def test_start_failure_is_distinct_from_started_nonzero_exit(tmp_path):
    async def scenario():
        module = api()
        with pytest.raises(module.PublicationProcessStartError) as caught:
            await module.run_publication_process([str(tmp_path / 'missing-executable')], cwd=tmp_path, env={}, timeout=1)
        assert caught.value.started is False
        assert caught.value.cleanup_confirmed is True
    asyncio.run(scenario())


def test_output_is_bounded_while_both_pipes_are_drained_without_deadlock(tmp_path):
    async def scenario():
        result = await api().run_publication_process([sys.executable, '-c',
            "import os; os.write(1,b'a'*3000000); os.write(2,b'b'*3000000)"],
            cwd=tmp_path, env={}, timeout=5, output_limit=4096)
        assert result.returncode == 0
        assert len(result.stdout.encode()) <= 4096 and len(result.stderr.encode()) <= 4096
        assert result.output_truncated is True
    asyncio.run(scenario())


def test_invalid_utf8_replacement_cannot_expand_the_final_output_budget(tmp_path):
    async def scenario():
        result = await api().run_publication_process([sys.executable, '-c',
            "import os; os.write(1,bytes([255])*4096)"], cwd=tmp_path, env={}, timeout=3, output_limit=4096)
        assert len(result.stdout.encode('utf-8')) <= 4096
        assert result.output_truncated is True
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX TERM-ignore and process-group integration')
def test_timeout_kills_three_generations_before_returning(tmp_path):
    async def scenario():
        try:
            result = await api().run_publication_process(process_tree(tmp_path), cwd=tmp_path,
                env=dict(os.environ), timeout=.5, terminate_grace=.1)
            assert result.timed_out is True and result.returncode != 0 and result.cleanup_confirmed is True
            await assert_tree_stopped(tmp_path)
        finally:
            emergency_cleanup(tmp_path)
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX TERM-ignore and process-group integration')
def test_repeated_cancel_waits_for_actual_group_cleanup_before_propagation(tmp_path):
    async def scenario():
        task = None
        try:
            task = asyncio.create_task(api().run_publication_process(process_tree(tmp_path), cwd=tmp_path,
                env=dict(os.environ), timeout=10, terminate_grace=.35))
            await wait_file(tmp_path / 'leaf.pid')
            task.cancel()
            await asyncio.sleep(.05)
            task.cancel()
            await asyncio.sleep(.05)
            assert not task.done(), 'a second cancellation bypassed the termination grace/cleanup barrier'
            with pytest.raises(asyncio.CancelledError):
                await task
            await assert_tree_stopped(tmp_path)
        finally:
            emergency_cleanup(tmp_path)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX orphaned group integration')
def test_successful_root_exit_does_not_leave_running_descendants(tmp_path):
    async def scenario():
        try:
            result = await api().run_publication_process(process_tree(tmp_path, root_exits=True), cwd=tmp_path,
                env=dict(os.environ), timeout=4, terminate_grace=.1)
            assert result.returncode == 0 and result.timed_out is False
            await assert_tree_stopped(tmp_path)
        finally:
            emergency_cleanup(tmp_path)
    asyncio.run(scenario())


def test_preflight_denial_happens_before_real_process_start(tmp_path):
    async def scenario():
        def denied():
            raise PermissionError('role changed')
        with pytest.raises(PermissionError):
            await api().run_publication_process([sys.executable, '-c', "open('started','w').write('yes')"],
                cwd=tmp_path, env={}, timeout=1, preflight=denied)
        assert not (tmp_path / 'started').exists()
    asyncio.run(scenario())


def test_cancelling_async_preflight_does_not_start_subprocess(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        async def preflight():
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(api().run_publication_process([sys.executable, '-c', "open('started','w').write('yes')"],
            cwd=tmp_path, env={}, timeout=1, preflight=preflight))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not (tmp_path / 'started').exists()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX local process integration')
def test_observation_failure_is_explicit_and_still_reaps_direct_child(tmp_path, monkeypatch):
    async def scenario():
        module = api()
        async def unobservable(pid):
            raise module.PublicationProcessCleanupError('fixture cannot observe', pid=pid)
        monkeypatch.setattr(module, '_posix_live', unobservable)
        with pytest.raises(module.PublicationProcessCleanupError) as caught:
            await module.run_publication_process([sys.executable, '-c', 'import time; time.sleep(60)'],
                cwd=tmp_path, env={}, timeout=.1, terminate_grace=.1)
        assert caught.value.started is True and caught.value.cleanup_confirmed is False
        assert not running(caught.value.pid)
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX local process integration')
def test_cancel_during_spawn_return_race_still_owns_and_cleans_started_tree(tmp_path, monkeypatch):
    async def scenario():
        module = api()
        create = asyncio.create_subprocess_exec
        launched, release = asyncio.Event(), asyncio.Event()
        async def delayed_create(*args, **kwargs):
            process = await create(*args, **kwargs)
            if args[0] == sys.executable:
                launched.set()
                await release.wait()
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed_create)
        task = asyncio.create_task(module.run_publication_process(process_tree(tmp_path), cwd=tmp_path,
            env=dict(os.environ), timeout=10, terminate_grace=.1))
        try:
            await launched.wait()
            await wait_file(tmp_path / 'leaf.pid')
            task.cancel()
            await asyncio.sleep(.02)
            task.cancel()
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await assert_tree_stopped(tmp_path)
        finally:
            release.set()
            emergency_cleanup(tmp_path)
    asyncio.run(scenario())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX local process integration')
def test_unexpected_runtime_error_after_spawn_cleans_tree_before_propagating(tmp_path, monkeypatch):
    async def scenario():
        module = api()
        async def broken_wait(process):
            await wait_file(tmp_path / 'leaf.pid')
            raise RuntimeError('fixture monitor failed')
        monkeypatch.setattr(module, '_wait_direct', broken_wait)
        try:
            with pytest.raises(RuntimeError, match='fixture monitor failed'):
                await module.run_publication_process(process_tree(tmp_path), cwd=tmp_path,
                    env=dict(os.environ), timeout=10, terminate_grace=.1)
            await assert_tree_stopped(tmp_path)
        finally:
            emergency_cleanup(tmp_path)
    asyncio.run(scenario())


def test_windows_taskkill_failure_cannot_confirm_a_tracked_orphan_has_stopped(monkeypatch):
    async def scenario():
        module = api()
        root_alive = True
        called = []
        async def control(argv, timeout=4):
            nonlocal root_alive
            if str(argv[0]).endswith('taskkill.exe'):
                called.append(argv)
                if argv[argv.index('/PID') + 1] == '101':
                    root_alive = False
                # The child refuses termination. Parent exit alone cannot be
                # taken as confirmation that this tracked child is gone.
                return 1, b'', b'access denied'
            rows = ([{'ProcessId': 101, 'ParentProcessId': 1}] if root_alive else [])
            rows.append({'ProcessId': 202, 'ParentProcessId': 101 if root_alive else 1})
            return 0, json.dumps(rows).encode(), b''
        monkeypatch.setattr(module, '_control', control)
        class Process:
            pid = 101
        with pytest.raises(module.PublicationProcessCleanupError):
            await module._terminate_windows(Process())
        assert all('/T' in argv and '/F' in argv for argv in called)
    asyncio.run(scenario())
