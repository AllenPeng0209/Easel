"""Explicit status refresh reads one prior ACK under a profile lease; it never republishes."""
import ast
import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from fastapi import HTTPException

from easel.projects import project_context
from easel.request_identity import actor_context
from easel.publication_process import PublicationProcessCleanupError
from tests.test_publication_dispatch import dispatch_environment as _dispatch_environment, ACCOUNT
import web.app as web

dispatch_environment = _dispatch_environment
pytestmark = pytest.mark.access_integration


def refresh_available():
    assert hasattr(web.PublicationWorkflow, 'refresh'), 'Missing explicit readonly publication refresh'


async def existing_submission(root, *, ack=True, platform='douyin'):
    workflow = web._publication_workflow()
    data = {'title': 'Reviewed cat', 'body': 'Reviewed caption', 'tags': 'cat', 'persona': '',
            'media': ['topic/clip.mp4'] if platform == 'douyin' else [], 'aiGenerated': True}
    record = await workflow.prepare(platform, data)
    workflow.decide(record['id'], True, record['payloadHash'])
    record = workflow.call('claim', record['id'], current_subject=record['subject'])
    directory = workflow.operation_dir(record)
    directory.mkdir(parents=True, mode=0o700)
    from publication_receipt import PublicationReceiptFiles, PublicationReceiptReporter
    context = web._publication_receipt_context(record)
    files = PublicationReceiptFiles(directory, context)
    args = SimpleNamespace(receipt_context=str(files.context_path), receipt_file=str(files.receipt_path))
    reporter = PublicationReceiptReporter.from_args(args, platform)
    account = {'id': ACCOUNT['id'], 'authenticated': True}
    reporter.record('submit_attempted', account=account)
    if ack:
        reporter.record('platform_ack', account=account, ack={
            'kind': 'post', 'id': 'post-id-one', 'source': 'provider_response', 'accepted': True, 'state': 'reviewing'})
        now = int(time.time() * 1000)
        matched = {'platform_content_id': 'post-id-one', 'title': 'Reviewed cat', 'status': 'reviewing'}
        reporter.record('readback', account=account, readback={'outcome': 'verified', 'matched': matched,
            'candidates': [matched], 'evidence': {'method': 'authenticated_readback', 'account': account,
                'matchMethod': 'ack_id', 'unique': True, 'statusSource': 'explicit', 'visibility': 'reviewing',
                'baselineCaptured': True, 'sinceMs': now - 100, 'checkedAtMs': now}})
    result = web.normalize_receipt(context, files.read())
    finished = workflow.call('finish', record['id'], operation_id=record['operationId'], result=result)
    return workflow, finished, files


def native_status(workflow, events):
    tree = ast.parse(Path(web.__file__).read_text())
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'publication_status' and [arg.arg for arg in node.args.args] == ['approval_id', 'call_id'])
    namespace = dict(vars(web), _publication_workflow=lambda: workflow,
        publication_event=lambda record, **binding: (events.append(binding), record)[1])
    exec(compile(ast.Module(body=[node], type_ignores=[]), web.__file__, 'exec'), namespace)
    return namespace['publication_status']


def fake_refresh(monkeypatch, *, public=True, error=None, waiting=None, release=None, before_launch=None):
    calls = []

    async def process(argv, *, preflight=None, **kwargs):
        assert Path(argv[1]).name == 'douyin_publish.py' and argv[2] == 'refresh-receipt'
        assert not any(value in argv for value in ['publish', 'publish-video', '--exec', '--video', '--images', '--title', '--content'])
        assert 'post-id-one' not in argv, 'External IDs must be read from the original private receipt, never passed by callers'
        if before_launch:
            before_launch()
        if preflight:
            value = preflight()
            if inspect.isawaitable(value):
                await value
        calls.append(argv)
        if waiting is not None:
            waiting.set()
        if release is not None:
            await release.wait()
        if error:
            raise error
        from publication_receipt import PublicationReceiptReporter
        reporter = PublicationReceiptReporter.from_args(SimpleNamespace(
            receipt_context=argv[argv.index('--receipt-context') + 1],
            receipt_file=argv[argv.index('--receipt-file') + 1]), 'douyin')
        now = int(time.time() * 1000)
        account = {'id': ACCOUNT['id'], 'authenticated': True}
        matched = {'platform_content_id': 'post-id-one', 'title': 'Reviewed cat',
            'status': 'published' if public else 'reviewing', 'published_at_ms': now - 10}
        reporter.record('readback', account=account, readback={'outcome': 'verified', 'matched': matched,
            'candidates': [matched], 'evidence': {'method': 'authenticated_readback', 'account': account,
                'matchMethod': 'ack_id', 'unique': True, 'statusSource': 'explicit',
                'visibility': 'public' if public else 'reviewing', 'baselineCaptured': True,
                'sinceMs': now - 100, 'checkedAtMs': now}})
        return SimpleNamespace(returncode=0, timed_out=False, output_truncated=False,
            stdout='Public URL in stdout is not evidence', stderr='')

    monkeypatch.setattr(web, 'run_publication_process', process)
    return calls


def test_native_status_explicitly_refreshes_the_original_operation_and_ordinary_get_never_starts_a_process(dispatch_environment, monkeypatch):
    refresh_available()
    root, actor, *_ = dispatch_environment
    calls = fake_refresh(monkeypatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await existing_submission(root)
            assert workflow.get(record['id'])['result']['status'] == 'submitted' and calls == []
            events = []
            final = await native_status(workflow, events)(record['id'], 'same-status-call')
            assert final['id'] == record['id'] and final['operationId'] == record['operationId']
            assert final['payloadHash'] == record['payloadHash'] and final['subject'] == record['subject']
            assert final['result']['status'] == 'confirmed'
            assert final['result']['externalId'] == 'post-id-one'
            assert final['result']['reason'] == 'public_readback_confirmed'
            assert len(calls) == 1 and events == [{'call_id': 'same-status-call', 'tool_name': 'easel_publication_status'}]
            assert not workflow.store().profile_busy('douyin', project_id='default')
            await workflow.refresh(record['id'])
            assert len(calls) == 1, 'A confirmed operation never reopens the browser'

    asyncio.run(scenario())


@pytest.mark.parametrize('ack,platform', [(False, 'douyin'), (True, 'zhihu')])
def test_refresh_cannot_open_a_browser_without_original_ack_or_on_an_unimplemented_platform(dispatch_environment, monkeypatch, ack, platform):
    refresh_available()
    root, actor, *_ = dispatch_environment
    calls = fake_refresh(monkeypatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await existing_submission(root, ack=ack, platform=platform)
            final = await workflow.refresh(record['id'])
            assert final['operationId'] == record['operationId'] and final['payloadHash'] == record['payloadHash']
            assert final['result']['status'] == ('submitted' if ack else 'unknown')
            assert calls == []

    asyncio.run(scenario())


def test_explicit_refresh_endpoint_is_scoped_and_uses_the_same_existing_intent(dispatch_environment, monkeypatch):
    refresh_available()
    assert hasattr(web, 'api_refresh_publication_approval'), 'Missing explicit UI refresh endpoint'
    root, actor, access, *_ = dispatch_environment
    calls = fake_refresh(monkeypatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await existing_submission(root)
            other = access.create_user('refresh-other', 'temporary-test-password')
            access.set_member('default', other['id'], 'operator')
            with actor_context(other):
                with pytest.raises(HTTPException) as forbidden:
                    await web.api_refresh_publication_approval(record['id'])
                assert forbidden.value.status_code == 404
            with project_context('foreign-ip', root):
                with pytest.raises(HTTPException):
                    await web.api_refresh_publication_approval(record['id'])
            assert calls == []
            final = await web.api_refresh_publication_approval(record['id'])
            assert final['operationId'] == record['operationId'] and len(calls) == 1

    asyncio.run(scenario())


def test_duplicate_or_frequent_refreshes_do_not_start_another_profile_process(dispatch_environment, monkeypatch):
    refresh_available()
    root, actor, *_ = dispatch_environment

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = fake_refresh(monkeypatch, public=False, waiting=started, release=release)
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await existing_submission(root)
            task = asyncio.create_task(workflow.refresh(record['id']))
            try:
                await asyncio.wait_for(started.wait(), 1)
                assert workflow.store().profile_busy('douyin', project_id='default')
                with pytest.raises(HTTPException) as busy:
                    await workflow.refresh(record['id'])
                assert busy.value.status_code == 409 and len(calls) == 1
                release.set()
                result = await task
                assert result['result']['status'] == 'submitted'
                await workflow.refresh(record['id'])
                assert len(calls) == 1, 'Completed reads are throttled, without changing their original result'
            finally:
                release.set()
                if not task.done():
                    await task

    asyncio.run(scenario())


def test_unconfirmed_readback_process_cleanup_keeps_a_durable_profile_lock_without_changing_submission(dispatch_environment, monkeypatch):
    refresh_available()
    root, actor, *_ = dispatch_environment
    calls = fake_refresh(monkeypatch, error=PublicationProcessCleanupError('unconfirmed fixture'))

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await existing_submission(root)
            with pytest.raises(HTTPException) as blocked:
                await workflow.refresh(record['id'])
            assert blocked.value.status_code == 409
            assert len(calls) == 1 and workflow.store().profile_busy('douyin', project_id='default')
            current = workflow.get(record['id'])
            assert current['operationId'] == record['operationId'] and current['result']['status'] == 'submitted'
            with pytest.raises(HTTPException):
                await workflow.refresh(record['id'])
            assert len(calls) == 1

    asyncio.run(scenario())


def test_revoked_permission_before_readback_spawn_releases_unused_lease_and_launches_nothing(dispatch_environment, monkeypatch):
    refresh_available()
    root, actor, access, *_ = dispatch_environment
    calls = fake_refresh(monkeypatch, before_launch=lambda: access.set_member('default', actor['id'], 'viewer'))

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await existing_submission(root)
            with pytest.raises(HTTPException) as revoked:
                await workflow.refresh(record['id'])
            assert revoked.value.status_code == 403
            assert calls == [] and not workflow.store().profile_busy('douyin', project_id='default')

    asyncio.run(scenario())


def test_ambiguous_submission_never_refreshes_and_cannot_release_an_already_owned_profile_lease(dispatch_environment, monkeypatch):
    refresh_available()
    root, actor, *_ = dispatch_environment
    calls = fake_refresh(monkeypatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, files = await existing_submission(root)
            snapshot = files.read()
            snapshot['submissionAmbiguous'] = True
            files.receipt_path.write_text(json.dumps(snapshot))
            final = await workflow.refresh(record['id'])
            assert calls == [] and final['result']['status'] != 'confirmed'
            assert any(item['result'].get('reason') == 'ambiguous_submission' for item in final.get('observations', []))
            assert final['operationId'] == record['operationId'] and final['payloadHash'] == record['payloadHash']
            assert not workflow.store().profile_busy('douyin', project_id='default')
            lease = workflow.call('reserve_refresh', record['id'], operation_id=record['operationId'],
                payload_hash=record['payloadHash'], min_interval_ms=0)
            assert lease['reserved']
            with pytest.raises(HTTPException) as busy:
                await workflow.refresh(record['id'])
            assert busy.value.status_code == 409 and calls == []
            assert workflow.call('refresh_owned', record['id'], lease['token'],
                operation_id=record['operationId'], payload_hash=record['payloadHash'])

    asyncio.run(scenario())


def test_ack_id_substitution_is_rejected_before_any_readback_process(dispatch_environment, monkeypatch):
    refresh_available()
    root, actor, *_ = dispatch_environment
    calls = fake_refresh(monkeypatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, files = await existing_submission(root)
            snapshot = files.read()
            snapshot['ack']['id'] = 'a-different-post'
            files.receipt_path.write_text(json.dumps(snapshot))
            final = await workflow.refresh(record['id'])
            assert calls == []
            assert final['operationId'] == record['operationId'] and final['result']['externalId'] == 'post-id-one'

    asyncio.run(scenario())


def test_cancellation_releases_readback_lease_only_after_owned_process_cleanup(dispatch_environment, monkeypatch):
    refresh_available()
    root, actor, *_ = dispatch_environment

    async def scenario():
        started, cleaning, clean = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def process(argv, *, preflight=None, **kwargs):
            preflight()
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await clean.wait()
                raise

        monkeypatch.setattr(web, 'run_publication_process', process)
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await existing_submission(root)
            task = asyncio.create_task(workflow.refresh(record['id']))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            await asyncio.wait_for(cleaning.wait(), 1)
            assert workflow.store().profile_busy('douyin', project_id='default')
            clean.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not workflow.store().profile_busy('douyin', project_id='default')
            assert workflow.get(record['id'])['result']['status'] == 'submitted'

    asyncio.run(scenario())
