import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from easel.publication_approvals import PublicationApprovalStore
from easel.superagent_effects import SuperagentEffectError, SuperagentEffectStore
from tests.test_publication_dispatch import dispatch_environment


SCOPE = {'actor_id': 'operator', 'project_id': 'ip-one'}
SUBJECT = {
    'project': {'id': 'ip-one'}, 'platform': {'id': 'douyin'}, 'account': {'id': 'account-one'},
    'mode': 'publish_now', 'title': 'Review me', 'body': 'Version one', 'media': [],
}


def setup(path):
    approvals = PublicationApprovalStore(path)
    approval = approvals.create(SUBJECT, **SCOPE)
    effects = SuperagentEffectStore(path)
    effects.enqueue('ticket-1', 'publish', approval_id=approval['id'], subject=SUBJECT, **SCOPE)
    return approvals, approval, effects


def approve(approvals, approval):
    approvals.decide(approval['id'], True, approval['payloadHash'], **SCOPE)


def test_dispatch_is_approval_bound_and_reserved_once_across_workers(tmp_path):
    approvals, approval, effects = setup(tmp_path)
    assert effects.reserve('ticket-1', 'publish', approvals=approvals, **SCOPE)['state'] == 'waiting_approval'
    approve(approvals, approval)
    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = list(pool.map(
            lambda _: SuperagentEffectStore(tmp_path).reserve('ticket-1', 'publish', approvals=approvals, **SCOPE),
            range(8),
        ))
    assert sum(result['dispatch'] for result in attempts) == 1
    assert all(result['state'] == 'in_flight' for result in attempts)
    with pytest.raises(SuperagentEffectError) as denied:
        effects.reserve('ticket-1', 'publish', approvals=approvals, **{**SCOPE, 'project_id': 'other'})
    assert denied.value.status_code == 404


def test_recovery_does_not_resend_after_an_ambiguous_dispatch(tmp_path):
    approvals, approval, effects = setup(tmp_path)
    approve(approvals, approval)
    assert effects.reserve('ticket-1', 'publish', approvals=approvals, **SCOPE)['dispatch']
    restored = SuperagentEffectStore(tmp_path)
    assert restored.reconcile('ticket-1', 'publish', approvals=approvals, **SCOPE)['state'] == 'uncertain'
    assert not restored.reserve('ticket-1', 'publish', approvals=approvals, **SCOPE)['dispatch']
    claimed = approvals.claim(approval['id'], SUBJECT, **SCOPE)
    operation = claimed['operationId']
    approvals.finish(approval['id'], operation, {'status': 'unknown'}, **SCOPE)
    assert restored.reconcile('ticket-1', 'publish', approvals=approvals, **SCOPE)['state'] == 'uncertain'
    approvals.observe(approval['id'], operation, {
        'status': 'confirmed', 'platform': 'douyin', 'accountId': 'account-one',
        'mode': 'publish_now', 'evidenceMethod': 'authenticated_readback', 'publicationState': 'public',
        'externalId': 'post-123', 'operationId': operation, 'payloadHash': approval['payloadHash'],
    }, **SCOPE)
    result = restored.reconcile('ticket-1', 'publish', approvals=approvals, **SCOPE)
    assert result['state'] == 'confirmed' and result['receipt']['externalId'] == 'post-123'
    assert not restored.reserve('ticket-1', 'publish', approvals=approvals, **SCOPE)['dispatch']


def test_changed_payload_and_reused_grant_fail_closed(tmp_path):
    approvals, approval, effects = setup(tmp_path)
    approve(approvals, approval)
    changed = {**SUBJECT, 'body': 'Version two'}
    with pytest.raises(SuperagentEffectError):
        effects.enqueue('ticket-1', 'publish', approval_id=approval['id'], subject=changed, **SCOPE)
    with pytest.raises(SuperagentEffectError):
        effects.enqueue('ticket-2', 'publish', approval_id=approval['id'], subject=SUBJECT, **SCOPE)
    different = approvals.create(changed, **SCOPE)
    approvals.decide(different['id'], True, different['payloadHash'], **SCOPE)
    effects.enqueue('ticket-1', 'changed', approval_id=different['id'], subject=SUBJECT, **SCOPE)
    with pytest.raises(SuperagentEffectError):
        effects.reserve('ticket-1', 'changed', approvals=approvals, **SCOPE)


def test_workflow_dispatch_uses_frozen_subject_and_recovery_only_reads(tmp_path):
    from easel.superagent_effects import execute_publication_effect

    approvals = PublicationApprovalStore(tmp_path)
    subject = {**SUBJECT, 'tags': 'tag', 'persona': 'profile', 'media': [{'artifactId': 'a' * 32}]}
    approval = approvals.create(subject, **SCOPE)
    approve(approvals, approval)
    calls = []

    class Workflow:
        def scope(self):
            return SCOPE

        def store(self):
            return approvals

        def get(self, approval_id):
            return approvals.get(approval_id, **SCOPE)

        async def start(self, platform, data, *, before_claim, wait_for_execution):
            assert wait_for_execution
            before_claim()
            calls.append((platform, data))
            approvals.claim(approval['id'], subject, **SCOPE)
            raise TimeoutError

    effects = SuperagentEffectStore(tmp_path)
    initial = asyncio.run(execute_publication_effect(Workflow(), effects, 'ticket-1', 'publish',
                                                    approval['id'], check_lease=lambda: None))
    assert initial['state'] == 'uncertain'
    result = asyncio.run(execute_publication_effect(Workflow(), SuperagentEffectStore(tmp_path),
                                                   'ticket-1', 'publish', approval['id'], check_lease=lambda: None))
    assert result['state'] == 'uncertain'
    assert calls == [('douyin', {'title': 'Review me', 'body': 'Version one', 'tags': 'tag', 'persona': 'profile',
                                 'artifactIds': ['a' * 32], 'approvalId': approval['id'],
                                 'payloadHash': approval['payloadHash']})]


def test_missing_account_does_not_grant_dispatch(tmp_path):
    approvals = PublicationApprovalStore(tmp_path)
    subject = {key: value for key, value in SUBJECT.items() if key != 'account'}
    approval = approvals.create(subject, **SCOPE)
    approve(approvals, approval)
    effects = SuperagentEffectStore(tmp_path)
    effects.enqueue('ticket-1', 'publish', approval_id=approval['id'], subject=subject, **SCOPE)
    with pytest.raises(SuperagentEffectError, match='账号'):
        effects.reserve('ticket-1', 'publish', approvals=approvals, **SCOPE)


def test_process_exit_between_platform_claim_and_receipt_never_reissues(tmp_path):
    import subprocess
    import sys

    approvals, approval, effects = setup(tmp_path)
    approve(approvals, approval)
    # Leave without Python cleanup after the dispatcher has recorded the intent.
    script = '''
import os
import sys
from pathlib import Path
from easel.publication_approvals import PublicationApprovalStore
from easel.superagent_effects import SuperagentEffectStore

path = Path(sys.argv[1])
approval_id = sys.argv[2]
scope = {'actor_id': 'operator', 'project_id': 'ip-one'}
approvals = PublicationApprovalStore(path)
record = approvals.get(approval_id, **scope)
effects = SuperagentEffectStore(path)
assert effects.reserve('ticket-1', 'publish', approvals=approvals, **scope)['dispatch']
assert approvals.claim(approval_id, record['subject'], **scope)['claimed']
os._exit(73)
'''
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path), approval['id']], timeout=10)
    assert child.returncode == 73
    restored = SuperagentEffectStore(tmp_path)
    recovered = restored.reconcile('ticket-1', 'publish', approvals=approvals, **SCOPE)
    assert recovered['state'] == 'uncertain' and recovered['operationId']
    assert restored.reserve('ticket-1', 'publish', approvals=approvals, **SCOPE)['dispatch'] is False


@pytest.mark.access_integration
def test_revoked_after_account_probe_does_not_claim_or_dispatch(dispatch_environment, monkeypatch):
    from easel.projects import project_context
    from easel.request_identity import actor_context
    from easel.superagent_effects import execute_publication_effect
    import web.app as web

    root, actor, *_ = dispatch_environment
    may_publish = True
    original_account = web._publication_account

    async def revoked_account(platform):
        nonlocal may_publish
        account = await original_account(platform)
        may_publish = False
        return account

    def check_lease():
        if not may_publish:
            raise SuperagentEffectError('任务发布许可已撤回')

    async def scenario():
        nonlocal may_publish
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            approval = await workflow.prepare('zhihu', {'title': 'Approved draft', 'body': 'Local test'})
            workflow.decide(approval['id'], True, approval['payloadHash'])
            monkeypatch.setattr(web, '_publication_account', revoked_account)
            effects = SuperagentEffectStore(root)
            with pytest.raises(SuperagentEffectError, match='已撤回'):
                await execute_publication_effect(workflow, effects, 'ticket-1', 'publish', approval['id'],
                                                 check_lease=check_lease)
            assert workflow.get(approval['id'])['operationId'] is None
            may_publish = True
            assert (await execute_publication_effect(workflow, effects, 'ticket-1', 'publish', approval['id'],
                                                     check_lease=check_lease))['state'] == 'uncertain'
            assert workflow.get(approval['id'])['operationId'] is None

    asyncio.run(scenario())


@pytest.mark.access_integration
def test_final_probe_revocation_prevents_platform_dispatch(dispatch_environment, monkeypatch):
    from easel.projects import project_context
    from easel.request_identity import actor_context
    from easel.superagent_effects import execute_publication_effect
    import web.app as web

    root, actor, *_ = dispatch_environment
    probes = 0
    original_account = web._publication_account

    async def account(platform):
        nonlocal probes
        probes += 1
        return await original_account(platform)

    def check_lease():
        if probes >= 3:
            raise SuperagentEffectError('任务发布许可已撤回')

    async def scenario():
        with actor_context(actor), project_context('default', root):
            monkeypatch.setattr(web, '_publication_account', account)
            workflow = web._publication_workflow()
            approval = await workflow.prepare('zhihu', {'title': 'Approved draft', 'body': 'Local test'})
            workflow.decide(approval['id'], True, approval['payloadHash'])
            effects = SuperagentEffectStore(root)
            await execute_publication_effect(workflow, effects, 'ticket-1', 'publish', approval['id'],
                                             check_lease=check_lease)
            record = workflow.get(approval['id'])
            assert record['operationId']
            assert record['result']['status'] == 'failed' and record['result']['notStarted'] is True

    asyncio.run(scenario())


@pytest.mark.access_integration
def test_task_cancel_waits_for_dispatch_cleanup_and_retains_unknown_receipt(dispatch_environment, monkeypatch):
    from easel.projects import project_context
    from easel.request_identity import actor_context
    from easel.superagent_effects import execute_publication_effect
    import web.app as web

    root, actor, *_ = dispatch_environment

    async def scenario():
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def dispatch(platform, request, **kwargs):
            calls.append(kwargs['operation_id'])
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await release.wait()

        monkeypatch.setattr(web, '_dispatch_publish', dispatch)
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            approval = await workflow.prepare('zhihu', {'title': 'Approved draft', 'body': 'Local test'})
            workflow.decide(approval['id'], True, approval['payloadHash'])
            effects = SuperagentEffectStore(root)
            execute = lambda: execute_publication_effect(workflow, effects, 'ticket-1', 'publish', approval['id'],
                                                         check_lease=lambda: None)
            active = asyncio.create_task(execute())
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                active.cancel()
                await asyncio.wait_for(cleaning.wait(), timeout=2)
                assert not active.done()
                record = workflow.get(approval['id'])
                assert record['result']['processState'] == 'stopping'
                release.set()
                result = await asyncio.wait_for(active, timeout=2)
                assert result['state'] == 'uncertain'
                assert workflow.get(approval['id'])['result']['status'] == 'unknown'
                assert (await execute())['state'] == 'uncertain'
                assert len(calls) == 1
            finally:
                release.set()
                if not active.done():
                    active.cancel()
                    await asyncio.gather(active, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.access_integration
def test_revocation_during_dispatch_probe_blocks_process_start(dispatch_environment, monkeypatch):
    from easel.projects import project_context
    from easel.request_identity import actor_context
    from easel.superagent_effects import execute_publication_effect
    import web.app as web

    root, actor, *_ = dispatch_environment
    probes = 0
    original_account = web._publication_account

    async def account(platform):
        nonlocal probes
        probes += 1
        return await original_account(platform)

    def check_lease():
        if probes >= 4:
            raise SuperagentEffectError('任务发布许可已撤回')

    async def runner(argv, *, preflight, **kwargs):
        preflight()
        pytest.fail('Revoked task reached the subprocess boundary')

    async def scenario():
        with actor_context(actor), project_context('default', root):
            monkeypatch.setattr(web, '_publication_account', account)
            monkeypatch.setattr(web, 'run_publication_process', runner)
            workflow = web._publication_workflow()
            approval = await workflow.prepare('zhihu', {'title': 'Approved draft', 'body': 'Local test'})
            workflow.decide(approval['id'], True, approval['payloadHash'])
            effects = SuperagentEffectStore(root)
            result = await execute_publication_effect(workflow, effects, 'ticket-1', 'publish', approval['id'],
                                                      check_lease=check_lease)
            record = workflow.get(approval['id'])
            assert probes == 4
            assert record['result']['status'] == 'failed' and record['result']['notStarted'] is True
            assert result['state'] == 'failed' and result['dispatch'] is False

    asyncio.run(scenario())
