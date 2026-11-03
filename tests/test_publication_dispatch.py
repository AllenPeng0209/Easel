"""Real dispatch wiring with hard-whitelisted fake platform processes only."""
from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from easel.access import AccessStore
from easel.projects import project_context
from easel.request_identity import actor_context
import web.app as web


pytestmark = pytest.mark.access_integration
ACCOUNT = {'id':'id:verified-account-one', 'name':'Verified account'}


@pytest.fixture
def dispatch_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'outputs/_sessions')
    monkeypatch.setattr(web, 'PUBLISH_DIR', tmp_path / 'outputs/_publish')
    monkeypatch.setattr(web, '_publication_dir',
        lambda: tmp_path.parent / (tmp_path.name + '-host-publications'))
    monkeypatch.setattr(web, 'SCHEDULE_FILE', tmp_path / 'outputs/_schedule.json')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    (tmp_path / 'outputs/topic').mkdir(parents=True)
    image = tmp_path / 'outputs/topic/cover.png'
    image.write_bytes(b'APPROVED_IMAGE')
    video = tmp_path / 'outputs/topic/clip.mp4'
    video.write_bytes(b'APPROVED_VIDEO')
    access = AccessStore(tmp_path)
    actor, _ = access.setup('owner', 'temporary-dispatch-test-password', project_ids=['default'])
    replacement = access.create_user('replacement', 'temporary-replacement-password')
    access.set_member('default', replacement['id'], 'owner')
    async def account(platform):
        return dict(ACCOUNT)
    monkeypatch.setattr(web, '_publication_account', account)
    monkeypatch.setattr(web, '_mp_login_status', lambda:{'state':'success'})
    def unexpected_process(*args, **kwargs):
        pytest.fail('Dispatch tests must never start a real platform process')
    monkeypatch.setattr(web.subprocess, 'run', unexpected_process)
    async def unexpected_managed_process(*args, **kwargs):
        unexpected_process()
    monkeypatch.setattr(web, 'run_publication_process', unexpected_managed_process, raising=False)
    monkeypatch.setattr(web, '_record_successful_publish',
        lambda *args, **kwargs: pytest.fail('Runner exit zero is not a published-calendar receipt'))
    return tmp_path, actor, access, image, video


def fake_runner(monkeypatch, platform, *, returncode=0, timeout=False):
    calls = []
    allowed = {'zhihu':'web_publisher.py', 'wechat-oa':'weixin_mp_stats.py',
               'douyin':'douyin_publish.py', 'xiaohongshu':'xhs_publish.py'}
    async def run(command, *, preflight=None, **kwargs):
        if preflight:
            preflight()
        if not isinstance(command, list) or len(command) < 2:
            pytest.fail('Only an explicit runner argument array is allowed')
        script = Path(command[1]).name
        if script != allowed[platform] or not any(value in command for value in ('publish', 'publish-video')):
            pytest.fail(f'Process outside hard whitelist: {script}')
        calls.append({'kind':'platform', 'command':command})
        return SimpleNamespace(returncode=-9 if timeout else returncode, stdout='fake local runner result',
            stderr='', timed_out=timeout, output_truncated=False)
    monkeypatch.setattr(web, 'run_publication_process', run)
    return calls


def test_membership_revoked_during_mp_materialization_prevents_platform_dispatch(dispatch_environment, monkeypatch):
    root, actor, access, image, _ = dispatch_environment
    calls = fake_runner(monkeypatch, 'wechat-oa')
    import easel.publication_article as articles
    from easel.publication_media import capture_publication_media, stage_publication_media
    original = articles.materialize_publication_article
    materialized = []
    def revoke_after_materialization(*args, **kwargs):
        result = original(*args, **kwargs)
        materialized.append(result)
        access.set_member('default', actor['id'], 'viewer')
        return result
    monkeypatch.setattr(articles, 'materialize_publication_article', revoke_after_materialization)
    async def scenario():
        with actor_context(actor), project_context('default', root):
            media = capture_publication_media(web._chat_artifact_store(), paths=['topic/cover.png'],
                session_id='dispatch-review', turn_id='one', call_id='one')
            body = 'Reviewed body\n\n![Inline](outputs/topic/cover.png)'
            article = articles.prepare_publication_article('Reviewed', body, media,
                web.OUTPUTS_DIR, web._publication_article_converter)
            staged = stage_publication_media(web._chat_artifact_store(), media,
                web._publication_dir() / 'test-stage')
            with pytest.raises(HTTPException) as caught:
                await web._dispatch_publish('wechat-oa', web.PublishRequest(title='Reviewed', body=body),
                    media_paths=staged, operation_id='a' * 32, expected_account=ACCOUNT,
                    approved_article=article, approved_media=media, payload_hash='a' * 64)
            assert caught.value.status_code == 403
    asyncio.run(scenario())
    assert materialized and calls == []
    assert access.role(actor['id'], 'default') == 'viewer'
    assert not web.SCHEDULE_FILE.exists()


@pytest.mark.parametrize('platform', ['zhihu', 'wechat-oa', 'douyin', 'xiaohongshu'])
def test_zero_exit_without_machine_receipt_does_not_claim_submission(dispatch_environment, monkeypatch, platform):
    root, actor, _, image, video = dispatch_environment
    calls = fake_runner(monkeypatch, platform)
    path = video if platform == 'douyin' else image
    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            payload = {'title':'Reviewed', 'body':'Reviewed body', 'tags':'', 'persona':'',
                       'media':[path.relative_to(web.OUTPUTS_DIR).as_posix()], 'artifactIds':[]}
            record = await workflow.prepare(platform, payload, idempotency_key='local-zero-exit-test')
            workflow.decide(record['id'], True, record['payloadHash'])
            await workflow.start(platform, {**payload, 'approvalId':record['id'], 'payloadHash':record['payloadHash']})
            from web.publication_workflow import _TASKS
            await asyncio.gather(*list(_TASKS))
            return workflow.store().get(record['id'], actor_id=actor['id'], project_id='default')
    record = asyncio.run(scenario())
    assert record['result']['status'] == 'unknown'
    assert record['result']['reason'] == 'machine_receipt_missing'
    assert record['result']['runnerExitCode'] == 0
    assert len([call for call in calls if call['kind'] == 'platform']) == 1
    assert not web.SCHEDULE_FILE.exists()
    platform_call = next(call['command'] for call in calls if call['kind'] == 'platform')
    assert str(image) not in platform_call and str(video) not in platform_call
    if platform == 'douyin':
        status = Path(platform_call[platform_call.index('--status-file') + 1])
        sms = Path(platform_call[platform_call.index('--sms-code-file') + 1])
        assert status.name == 'runner.json' and sms.name == 'runner.code'
        assert status.parent == web._publication_dir() / 'operations' / actor['id'] / record['operationId']
        assert sms.parent == status.parent


@pytest.mark.parametrize('failure', ['nonzero', 'timeout'])
def test_platform_process_failure_stays_unknown_and_never_claims_not_started(dispatch_environment, monkeypatch, failure):
    root, actor, _, image, _ = dispatch_environment
    calls = fake_runner(monkeypatch, 'zhihu', returncode=1, timeout=failure == 'timeout')
    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            payload = {'title':'Reviewed', 'body':'Reviewed body', 'tags':'', 'persona':'',
                       'media':[image.relative_to(web.OUTPUTS_DIR).as_posix()], 'artifactIds':[]}
            record = await workflow.prepare('zhihu', payload, idempotency_key='local-failure-test')
            workflow.decide(record['id'], True, record['payloadHash'])
            await workflow.start('zhihu', {**payload, 'approvalId':record['id'], 'payloadHash':record['payloadHash']})
            from web.publication_workflow import _TASKS
            await asyncio.gather(*list(_TASKS))
            return workflow.store().get(record['id'], actor_id=actor['id'], project_id='default')
    record = asyncio.run(scenario())
    assert [call['kind'] for call in calls] == ['platform']
    assert record['result']['status'] == 'unknown'
    assert record['result'].get('notStarted') is not True
    assert not web.SCHEDULE_FILE.exists()


def test_internal_dispatch_without_operation_and_account_is_refused(dispatch_environment):
    root, actor, _, image, _ = dispatch_environment
    async def scenario():
        with actor_context(actor), project_context('default', root):
            with pytest.raises(HTTPException) as caught:
                await web._dispatch_publish('zhihu', web.PublishRequest(title='Unapproved'), media_paths=[str(image)])
            assert caught.value.status_code == 428
    asyncio.run(scenario())
