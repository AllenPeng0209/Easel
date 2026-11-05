"""Host XHS refresh keeps the original intent and launches a GET-only command."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from fastapi import HTTPException

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_publication_dispatch import dispatch_environment as _dispatch_environment, fake_runner
import web.app as web

dispatch_environment = _dispatch_environment
pytestmark = pytest.mark.access_integration
ACCOUNT = {'id': 'userId:fixture-xhs-owner', 'name': 'Fixture owner'}
NOTE = '6ad86a4600000000330118ff'
STAMP = {'xhsSessionGeneration': 2, 'xhsNativeAccountId': 1, 'xhsSessionHash': 'c' * 64}


async def submitted(monkeypatch, *, baseline=True, ambiguous=False):
    async def account(_platform): return dict(ACCOUNT)
    monkeypatch.setattr(web, '_publication_account', account)
    workflow = web._publication_workflow()
    record = await workflow.prepare('xiaohongshu', {'title': 'Reviewed film', 'body': 'AI创作｜虚构亲子故事',
        'tags': '', 'persona': '', 'media': ['topic/clip.mp4'], 'aiGenerated': True})
    workflow.decide(record['id'], True, record['payloadHash'])
    record = workflow.call('claim', record['id'], current_subject=record['subject'])
    directory = workflow.operation_dir(record); directory.mkdir(parents=True, mode=0o700)
    from publication_receipt import PublicationReceiptFiles, PublicationReceiptReporter
    context = web._publication_receipt_context(record)
    files = PublicationReceiptFiles(directory, context)
    reporter = PublicationReceiptReporter.from_args(SimpleNamespace(receipt_context=str(files.context_path),
        receipt_file=str(files.receipt_path)), 'xiaohongshu')
    identity = {'id': ACCOUNT['id'], 'authenticated': True}
    reporter.record('submit_attempted', account=identity)
    proof = {'outcome': 'unverified', 'matched': None, 'candidates': [], 'evidence': {
        'method': 'authenticated_readback', 'account': identity, 'matchMethod': 'ack_id',
        'unique': False, 'baselineCaptured': baseline, 'sinceMs': int(time.time() * 1000) - 100, **STAMP}}
    initial_proof = deepcopy(proof); initial_proof['evidence']['baselineCaptured'] = False
    reporter.record('submit_attempted', account=identity, readback=initial_proof)
    reporter.record('platform_ack', account=identity, ack={'kind': 'post', 'accepted': True,
        'source': 'provider_response', 'id': NOTE, 'state': 'submitted'}, readback=proof)
    if ambiguous: reporter.mark_submission_ambiguous()
    result = web.normalize_receipt(context, files.read())
    record = workflow.call('finish', record['id'], operation_id=record['operationId'], result=result)
    return workflow, record, files


def refresh_runner(monkeypatch, *, public=True, before=None):
    calls = []

    async def run(argv, *, preflight=None, **kwargs):
        assert Path(argv[1]).name == 'xhs_publish.py' and argv[2] == 'refresh-receipt'
        assert not any(x in argv for x in ('--exec', 'publish', 'publish-video', '--title', '--video', '--images'))
        assert NOTE not in argv
        if before: before()
        if preflight: preflight()
        calls.append(argv)
        from publication_receipt import PublicationReceiptReporter
        reporter = PublicationReceiptReporter.from_args(SimpleNamespace(
            receipt_context=argv[argv.index('--receipt-context') + 1],
            receipt_file=argv[argv.index('--receipt-file') + 1]), 'xiaohongshu')
        previous = reporter.snapshot(); evidence = deepcopy(previous['readback']['evidence'])
        evidence.update(checkedAtMs=int(time.time() * 1000), statusSource='explicit',
                        visibility='public' if public else 'reviewing', unique=True)
        matched = {'platform_content_id': NOTE, 'status': 'published' if public else 'reviewing',
                   'published_at_ms': evidence['sinceMs'] + 20 if public else None}
        reporter.record('readback', account=previous['account'], readback={
            'outcome': 'verified', 'matched': matched, 'candidates': [matched], 'evidence': evidence})
        return SimpleNamespace(returncode=0, timed_out=False, output_truncated=False,
            cleanup_confirmed=True, stdout='not authority', stderr='')

    monkeypatch.setattr(web, 'run_publication_process', run)
    return calls


def test_xhs_host_refreshes_only_original_ack_and_projects_only_strong_public_receipt(dispatch_environment, monkeypatch):
    root, actor, *_ = dispatch_environment; calls = refresh_runner(monkeypatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, original, _ = await submitted(monkeypatch)
            assert original['result']['status'] == 'submitted' and calls == []
            final = await workflow.refresh(original['id'])
            assert final['result']['status'] == 'confirmed'
            assert final['operationId'] == original['operationId'] and final['payloadHash'] == original['payloadHash']
            assert final['subject'] == original['subject'] and len(calls) == 1
            assert not workflow.store().profile_busy('xiaohongshu', project_id='default')
            await workflow.refresh(original['id'])
            assert len(calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('baseline,ambiguous', [(False, False), (True, True)])
def test_xhs_legacy_or_ambiguous_submission_never_starts_refresh_process(dispatch_environment, monkeypatch, baseline, ambiguous):
    root, actor, *_ = dispatch_environment; calls = refresh_runner(monkeypatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, original, _ = await submitted(monkeypatch, baseline=baseline, ambiguous=ambiguous)
            final = await workflow.refresh(original['id'])
            assert final['result']['status'] != 'confirmed' and calls == []
            assert final['operationId'] == original['operationId']
    asyncio.run(scenario())


def test_xhs_readback_stays_submitted_when_the_original_note_is_reviewing(dispatch_environment, monkeypatch):
    root, actor, *_ = dispatch_environment; calls = refresh_runner(monkeypatch, public=False)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, original, _ = await submitted(monkeypatch)
            final = await workflow.refresh(original['id'])
            assert final['result']['status'] == 'submitted' and len(calls) == 1
            assert final['result']['publicationState'] == 'reviewing'
    asyncio.run(scenario())


def test_xhs_account_or_actor_change_before_spawn_prevents_readback(dispatch_environment, monkeypatch):
    root, actor, access, *_ = dispatch_environment
    calls = refresh_runner(monkeypatch, before=lambda: access.set_member('default', actor['id'], 'viewer'))

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await submitted(monkeypatch)
            with pytest.raises(HTTPException) as error: await workflow.refresh(record['id'])
            assert error.value.status_code == 403 and calls == []
            assert not workflow.store().profile_busy('xiaohongshu', project_id='default')
    asyncio.run(scenario())


def test_public_xhs_dispatch_always_requests_public_and_baseline_gate(dispatch_environment, monkeypatch):
    root, actor, _, _, video = dispatch_environment
    calls = fake_runner(monkeypatch, 'xiaohongshu')

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            payload = {'title': 'Reviewed', 'body': 'Reviewed body', 'tags': '', 'persona': '',
                       'media': [video.relative_to(web.OUTPUTS_DIR).as_posix()], 'aiGenerated': True}
            record = await workflow.prepare('xiaohongshu', payload)
            workflow.decide(record['id'], True, record['payloadHash'])
            await workflow.start('xiaohongshu', {**payload, 'approvalId': record['id'], 'payloadHash': record['payloadHash']})
            from web.publication_workflow import _TASKS
            await asyncio.gather(*list(_TASKS))
    asyncio.run(scenario())
    assert len(calls) == 1 and calls[0]['command'].count('--require-public') == 1


@pytest.mark.parametrize('field,replacement', [
    ('sinceMs', 1_793_000_000_000), ('xhsSessionGeneration', 3),
    ('xhsNativeAccountId', 2), ('xhsSessionHash', 'd' * 64),
])
def test_host_rechecks_the_same_original_baseline_before_starting_readonly_process(dispatch_environment, monkeypatch, field, replacement):
    root, actor, *_ = dispatch_environment
    target = {}
    def replace():
        path = target['files'].receipt_path
        value = json.loads(path.read_text()); value['readback']['evidence'][field] = replacement
        path.write_text(json.dumps(value))
    calls = refresh_runner(monkeypatch, before=replace)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, files = await submitted(monkeypatch)
            target['files'] = files
            with pytest.raises(HTTPException) as changed:
                await workflow.refresh(record['id'])
            assert changed.value.status_code == 409 and calls == []
            assert not workflow.store().profile_busy('xiaohongshu', project_id='default')
    asyncio.run(scenario())


def test_other_actor_or_ip_cannot_refresh_the_xhs_operation(dispatch_environment, monkeypatch):
    root, actor, access, *_ = dispatch_environment; calls = refresh_runner(monkeypatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow, record, _ = await submitted(monkeypatch)
            other = access.create_user('xhs-other', 'temporary-other-test-password')
            access.set_member('default', other['id'], 'owner')
            with actor_context(other):
                with pytest.raises(HTTPException) as wrong: await workflow.refresh(record['id'])
                assert wrong.value.status_code == 404
            with project_context('foreign-ip', root):
                with pytest.raises(HTTPException): await workflow.refresh(record['id'])
            assert calls == []
    asyncio.run(scenario())
