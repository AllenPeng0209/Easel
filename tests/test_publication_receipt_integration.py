"""D30 real workflow/dispatcher tests; every platform process is hard-faked."""
import argparse
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

from tests.test_publication_dispatch import dispatch_environment, ACCOUNT
from easel.projects import project_context
from easel.request_identity import actor_context
import web.app as web

pytestmark = pytest.mark.access_integration


def run_case(environment, monkeypatch, *, platform='zhihu', evidence='missing', code=0):
    root, actor, _, image, video = environment
    monkeypatch.setitem(ACCOUNT, 'id', 'id:verified-account-one')
    seen = []
    scripts = {'zhihu': 'web_publisher.py', 'wechat-oa': 'weixin_mp_stats.py', 'bilibili': 'bili_upload.py'}
    def fake(command, **kwargs):
        assert isinstance(command, list) and len(command) > 1
        assert Path(command[1]).name == scripts[platform], 'unrecognized process blocked'
        seen.append(command)
        assert '--receipt-context' in command and '--receipt-file' in command
        context = Path(command[command.index('--receipt-context')+1])
        output = Path(command[command.index('--receipt-file')+1])
        host = json.loads(context.read_text())
        assert host['platform'] == platform and host['approvedAccountId'] == ACCOUNT['id']
        assert len(host['operationId']) == 32 and len(host['payloadHash']) == 64
        if evidence != 'missing':
            from publication_receipt import PublicationReceiptReporter
            reporter = PublicationReceiptReporter.from_args(argparse.Namespace(
                receipt_context=str(context), receipt_file=str(output)), platform)
            reporter.record('submit_attempted')
            account = {'id': ACCOUNT['id'], 'authenticated': True}
            if evidence == 'draft':
                reporter.record('platform_ack', ack={'kind':'draft','id':'draft-id-1','accepted':True,'source':'provider_response'})
            else:
                ack = {'kind':'post','id':'post-id-1','accepted':True,'source':'provider_response','state':'reviewing'}
                reporter.record('platform_ack', account=account, ack=ack)
                if evidence == 'public':
                    now = int(time.time()*1000)
                    matched = {'platform_content_id':'post-id-1','title':'Reviewed','status':'published','published_at_ms':now-100}
                    reporter.record('readback', readback={'outcome':'verified','matched':matched,'candidates':[matched],
                        'evidence':{'method':'authenticated_readback','account':account,'matchMethod':'ack_id',
                        'unique':True,'statusSource':'explicit','visibility':'public','baselineCaptured':True,
                        'sinceMs':now-500,'checkedAtMs':now}})
        return SimpleNamespace(returncode=code, stdout='发布成功 https://example.invalid/not-proof BV_FAKE', stderr='')
    monkeypatch.setattr(web.subprocess, 'run', fake)
    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            media = [str(image.relative_to(web.OUTPUTS_DIR))] if platform=='wechat-oa' else [str(video.relative_to(web.OUTPUTS_DIR))] if platform=='bilibili' else []
            payload = {'title':'Reviewed','body':'Exact approved body','tags':'','persona':'','media':media,'artifactIds':[]}
            record = await workflow.prepare(platform, payload)
            workflow.decide(record['id'], True, record['payloadHash'])
            await workflow.start(platform, {**payload,'approvalId':record['id'],'payloadHash':record['payloadHash']})
            from web.publication_workflow import _TASKS
            await asyncio.gather(*list(_TASKS))
            return workflow.get(record['id'])
    return asyncio.run(scenario()), seen


def test_zero_exit_and_success_logs_without_machine_receipt_stay_unknown(dispatch_environment, monkeypatch):
    record, seen = run_case(dispatch_environment, monkeypatch)
    assert len(seen)==1
    assert record['result']['status']=='unknown'
    assert record['result']['reason']=='machine_receipt_missing'
    assert 'detail' not in record['result']
    assert not web.SCHEDULE_FILE.exists()


def test_correlated_mp_ack_only_creates_draft(dispatch_environment, monkeypatch):
    record, seen = run_case(dispatch_environment, monkeypatch, platform='wechat-oa', evidence='draft')
    assert len(seen)==1
    assert record['result']['status']=='draft_created'
    assert record['result']['externalId']=='draft-id-1'
    assert record['result']['identityVerification']=='host_dispatch_only'
    assert not web.SCHEDULE_FILE.exists()


def test_provider_ack_under_review_is_submitted_not_public(dispatch_environment, monkeypatch):
    record, seen = run_case(dispatch_environment, monkeypatch, evidence='ack')
    assert len(seen)==1 and record['result']['status']=='submitted'
    assert record['result']['publicationState']=='reviewing'
    assert not web.SCHEDULE_FILE.exists()


def test_bilibili_dispatch_uses_existing_readback_wrapper(dispatch_environment, monkeypatch):
    record, seen = run_case(dispatch_environment, monkeypatch, platform='bilibili')
    assert len(seen)==1 and record['result']['status']=='unknown'
    assert '--exec' in seen[0] and '--video' in seen[0]
    assert not web.SCHEDULE_FILE.exists()


def test_only_strong_public_receipt_projects_calendar_once_without_redispatch(dispatch_environment, monkeypatch):
    record, seen = run_case(dispatch_environment, monkeypatch, evidence='public')
    assert record['result']['status']=='confirmed'
    assert record['result']['evidenceMethod']=='authenticated_readback'
    assert web.SCHEDULE_FILE.is_file()
    items=json.loads(web.SCHEDULE_FILE.read_text())
    assert len(items)==1
    assert items[0]['publicationOperationId']==record['operationId']
    assert items[0]['publicationPayloadHash']==record['payloadHash']
    root, actor, *_ = dispatch_environment
    with actor_context(actor), project_context('default',root):
        web._publication_workflow().get(record['id'])
        web._publication_workflow().get(record['id'])
    assert len(json.loads(web.SCHEDULE_FILE.read_text()))==1
    assert len(seen)==1


def test_readonly_lookup_accepts_late_ack_then_public_readback_without_resubmitting(dispatch_environment, monkeypatch):
    record, seen = run_case(dispatch_environment, monkeypatch)
    assert record['result']['status'] == 'unknown'
    root, actor, *_ = dispatch_environment
    with actor_context(actor), project_context('default', root):
        directory = web._publication_workflow().operation_dir(record)
        from publication_receipt import PublicationReceiptReporter
        reporter = PublicationReceiptReporter.from_args(argparse.Namespace(
            receipt_context=str(directory / 'receipt-context.json'),
            receipt_file=str(directory / 'receipt.json')), 'zhihu')
        account = {'id': ACCOUNT['id'], 'authenticated': True}
        reporter.record('submit_attempted', account=account)
        reporter.record('platform_ack', ack={'kind': 'post', 'id': 'late-post-id',
            'accepted': True, 'source': 'provider_response', 'state': 'reviewing'})
        submitted = web._publication_workflow().get(record['id'])
        assert submitted['result']['status'] == 'submitted'
        assert not web.SCHEDULE_FILE.exists()
        now = int(time.time() * 1000)
        matched = {'platform_content_id': 'late-post-id', 'title': 'Reviewed',
            'status': 'published', 'published_at_ms': now - 100}
        reporter.record('readback', readback={'outcome': 'verified', 'matched': matched,
            'candidates': [matched], 'evidence': {'method': 'authenticated_readback',
                'account': account, 'matchMethod': 'ack_id', 'unique': True,
                'statusSource': 'explicit', 'visibility': 'public', 'baselineCaptured': True,
                'sinceMs': now - 500, 'checkedAtMs': now}})
        confirmed = web._publication_workflow().get(record['id'])
        assert confirmed['result']['status'] == 'confirmed'
        replay = web._publication_workflow().get(record['id'])
        assert len(replay['observations']) == len(confirmed['observations'])
    assert len(seen) == 1
    assert len(json.loads(web.SCHEDULE_FILE.read_text())) == 1
