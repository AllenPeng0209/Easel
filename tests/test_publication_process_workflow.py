"""Local owned-process acceptance for the real publication workflow. No platforms."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.test_publication_dispatch import dispatch_environment
from easel.projects import project_context
from easel.request_identity import actor_context
import web.app as web

pytestmark = [pytest.mark.access_integration,
              pytest.mark.skipif(os.name == 'nt', reason='POSIX real process group acceptance')]


async def until(predicate, timeout=4):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, 'Owned local test process never reached its boundary'
        await asyncio.sleep(.01)


def local_runner(monkeypatch, root):
    """Both old and new boundaries can launch only this finite local fixture."""
    marker = root / 'owned-process.json'
    code = ('import json,os,signal,time;from pathlib import Path;'
            'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
            f'Path({str(marker)!r}).write_text(json.dumps({{"pid":os.getpid(),"pgid":os.getpgrp()}}));'
            'time.sleep(20)')
    command = [sys.executable, '-c', code]
    calls = []
    processes = []
    original_run = subprocess.run

    def validate(argv):
        assert isinstance(argv, list) and len(argv) > 1
        assert Path(argv[1]).name == 'web_publisher.py', 'Unrecognized platform command blocked'
        calls.append(argv)

    def legacy(argv, **kwargs):
        if argv and Path(argv[0]).name == 'ps':
            return original_run(argv, **kwargs)
        validate(argv)
        process = subprocess.Popen(command, cwd=root, env=dict(os.environ),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, text=True)
        processes.append(process)
        out, err = process.communicate(timeout=10)
        return SimpleNamespace(returncode=process.returncode, stdout=out, stderr=err)

    async def managed(argv, *, preflight=None, **kwargs):
        validate(argv)
        from easel.publication_process import run_publication_process
        return await run_publication_process(command, cwd=root, env=dict(os.environ),
            timeout=10, terminate_grace=.3, preflight=preflight)

    monkeypatch.setattr(web.subprocess, 'run', legacy)
    monkeypatch.setattr(web, 'run_publication_process', managed, raising=False)
    return marker, calls, processes


def test_cancel_keeps_profile_locked_until_real_runner_is_reaped(dispatch_environment, monkeypatch):
    root, actor, *_ = dispatch_environment
    marker, calls, processes = local_runner(monkeypatch, root)

    async def scenario():
        task = None
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            payload = {'title':'Owned local cancellation', 'body':'No social action', 'media':[]}
            record = await workflow.prepare('zhihu', payload)
            workflow.decide(record['id'], True, record['payloadHash'])
            from web.publication_workflow import _TASKS
            before = set(_TASKS)
            await workflow.start('zhihu', {**payload, 'approvalId':record['id'], 'payloadHash':record['payloadHash']})
            task = next(iter(set(_TASKS) - before))
            try:
                await until(marker.exists)
                task.cancel()
                await asyncio.sleep(.05)
                # The fixture ignores TERM: cancellation cannot release the
                # resource while the same owned runner is still being killed.
                assert workflow.store().profile_busy('zhihu', project_id='default'), 'Cancelled waiter released a still-running publication profile'
                current = workflow.get(record['id'])
                assert current['result']['status'] == 'executing'
                assert current['result']['processState'] == 'stopping'
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                final = workflow.get(record['id'])
                assert final['result']['status'] == 'unknown'
                assert final['result']['terminationConfirmed'] is True
                assert final['result']['processState'] == 'terminated'
                assert not workflow.store().profile_busy('zhihu', project_id='default')
                assert len(calls) == 1
            finally:
                if marker.exists():
                    facts = json.loads(marker.read_text())
                    assert facts['pid'] == facts['pgid']
                    try:
                        os.killpg(facts['pgid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if task and not task.done():
                    task.cancel()
                if task:
                    await asyncio.gather(task, return_exceptions=True)
                for process in processes:
                    await asyncio.to_thread(process.wait, timeout=3)
    asyncio.run(scenario())


def test_unconfirmed_process_cleanup_preserves_execution_lock_and_stops_sms_input(dispatch_environment, monkeypatch):
    root, actor, *_ = dispatch_environment
    import web.publication_workflow as workflow_module
    class UnconfirmedCleanup(RuntimeError):
        pass
    monkeypatch.setattr(workflow_module, 'PublicationProcessCleanupError', UnconfirmedCleanup, raising=False)
    async def cannot_confirm(*args, **kwargs):
        raise UnconfirmedCleanup('private diagnostic must not become a public success')
    monkeypatch.setattr(web, '_dispatch_publish', cannot_confirm)
    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            payload = {'title':'Cleanup unresolved', 'body':'No real process', 'media':[]}
            record = await workflow.prepare('zhihu', payload)
            workflow.decide(record['id'], True, record['payloadHash'])
            from web.publication_workflow import _TASKS
            before = set(_TASKS)
            await workflow.start('zhihu', {**payload,'approvalId':record['id'],'payloadHash':record['payloadHash']})
            await asyncio.gather(*(set(_TASKS)-before), return_exceptions=True)
            original = workflow.call('get', record['id'])
            status_path = workflow.status_path(original)
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(json.dumps({'state':'sms_required','message':'old runner prompt'}))
            final = workflow.get(record['id'])
            assert workflow.store().profile_busy('zhihu', project_id='default'), 'Unconfirmed cleanup must retain the profile lock'
            assert final['result']['status'] == 'executing'
            assert final['result']['processState'] == 'termination_unconfirmed'
            assert final['result']['terminationConfirmed'] is False
            assert 'runnerStatus' not in final
            with pytest.raises(HTTPException) as caught:
                workflow.sms('zhihu', record['id'], '123456')
            assert caught.value.status_code == 409
            assert not (status_path.parent / 'runner.code').exists()
            assert 'private diagnostic' not in json.dumps(final)
            replay = await workflow.start('zhihu', {**payload,'approvalId':record['id'],'payloadHash':record['payloadHash']})
            assert not replay.get('claimed')
    asyncio.run(scenario())


def test_managed_start_failure_is_not_reported_as_attempted_submission(dispatch_environment, monkeypatch):
    root, actor, *_ = dispatch_environment
    class NotStarted(RuntimeError):
        started = False
        cleanup_confirmed = True
    monkeypatch.setattr(web, 'PublicationProcessStartError', NotStarted, raising=False)
    async def unavailable(*args, **kwargs):
        raise NotStarted('fixture deliberately has no executable')
    monkeypatch.setattr(web, 'run_publication_process', unavailable, raising=False)
    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            payload = {'title':'No runner', 'body':'No social action', 'media':[]}
            record = await workflow.prepare('zhihu', payload)
            workflow.decide(record['id'], True, record['payloadHash'])
            from web.publication_workflow import _TASKS
            before=set(_TASKS)
            await workflow.start('zhihu', {**payload,'approvalId':record['id'],'payloadHash':record['payloadHash']})
            await asyncio.gather(*(set(_TASKS)-before), return_exceptions=True)
            final=workflow.get(record['id'])
            assert final['result']['status']=='failed'
            assert final['result']['notStarted'] is True
            assert final['result']['reason']=='runner_unavailable'
            assert not workflow.store().profile_busy('zhihu',project_id='default')
    asyncio.run(scenario())
