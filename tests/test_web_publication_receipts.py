"""Native publisher receipt boundaries; every platform/network boundary is denied."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/shared/scripts'
sys.path.insert(0, str(SCRIPTS))
import web_publisher
import platform_readback


class Recorder:
    enabled = True
    def __init__(self, timeline, *, enabled=True, fail_attempt=False):
        self.timeline = timeline
        self.enabled = enabled
        self.fail_attempt = fail_attempt
        self.events = []
    def require_approved_account(self, account):
        if not account or account.get('authenticated') is not True:
            raise ValueError('Missing authenticated account')
        self.timeline.append(('approved-account', account['id']))

    def record(self, phase, **fields):
        self.timeline.append(('receipt', phase))
        if self.fail_attempt and phase == 'submit_attempted':
            raise RuntimeError('private receipt write failed')
        self.events.append({'phase': phase, **fields})


@pytest.fixture(autouse=True)
def offline_boundaries(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('Publication receipt tests cannot call real HTTP/browser/platform processes')
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)
    monkeypatch.setattr(web_publisher.platform_readback, 'read_bilibili_account', denied)
    monkeypatch.setattr(web_publisher.platform_readback, 'capture_bilibili_snapshot', denied)
    monkeypatch.setattr(web_publisher.platform_readback, 'verify_bilibili_publish', denied)
    monkeypatch.setattr(web_publisher.platform_readback, 'read_kuaishou_account', denied)


def reporter_module(monkeypatch, recorder):
    factory = SimpleNamespace(from_args=lambda *_: recorder)
    monkeypatch.setitem(sys.modules, 'publication_receipt', SimpleNamespace(PublicationReceiptReporter=factory))
    return recorder


def browser_flow(monkeypatch, tmp_path, platform='zhihu', *, click_error=False, probe=None):
    timeline = []
    recorder = reporter_module(monkeypatch, Recorder(timeline))
    args = SimpleNamespace(platform=platform, media=None, title='Reviewed title', desc='Reviewed body', tags='',
        cover=None, profile_base=str(tmp_path), keep_open=False, headed=False, exec=True,
        receipt_context='private-context', receipt_file='private-receipt', allow_unsafe=False)
    if platform in ('kuaishou', 'weixin-channels'):
        media = tmp_path / 'video.mp4'; media.write_bytes(b'fixture-video'); args.media = str(media)
    page = SimpleNamespace(url='https://platform.test/write', pages=[], wait_for_timeout=lambda *_: None,
        goto=lambda *args, **kwargs: None, query_selector=lambda *_: None, screenshot=lambda **kwargs: None)
    def click(**kwargs):
        timeline.append(('click', platform))
        page.url = 'https://platform.test/p/one'
        if click_error: raise RuntimeError('click transport became uncertain')
    button = SimpleNamespace(click=click, scroll_into_view_if_needed=lambda: None)
    def evaluate(script, *args):
        if 'dispatchEvent(new MouseEvent' in script:
            timeline.append(('fallback-click', platform))
        return {}
    page.evaluate = evaluate
    browser = SimpleNamespace(pages=[page], close=lambda: timeline.append(('closed', platform)))
    if probe is not None:
        browser.new_page = lambda: probe
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *args, **kwargs: browser))
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(
        sync_playwright=lambda: contextlib.nullcontext(playwright), TimeoutError=TimeoutError))
    monkeypatch.setattr(web_publisher, '_settle_login', lambda *args: None)
    monkeypatch.setattr(web_publisher, '_is_logged_in', lambda *args: True)
    monkeypatch.setattr(web_publisher, '_probe_publish_auth', lambda *args: 'ok')
    monkeypatch.setattr(web_publisher, '_observe_channels_account', lambda *args: None)
    monkeypatch.setattr(web_publisher, '_authenticated_account', lambda *args, **kwargs: {'id': 'actual-self', 'name': 'Me'})
    monkeypatch.setattr(web_publisher, '_first_visible_el', lambda *args: button)
    monkeypatch.setattr(web_publisher.human_pace, 'pause_before_commit', lambda *_: None)
    monkeypatch.setattr(web_publisher.content_guard, 'guard_or_die', lambda *args, **kwargs: None)
    monkeypatch.setattr(web_publisher, '_resolve_steps', lambda *args: [
        {'action': 'js_click' if platform == 'kuaishou' else 'click', 'selector': 'publish', 'commit': True}])
    monkeypatch.setattr(web_publisher, '_readback_capture', lambda *args: {'old-post'})
    monkeypatch.setattr(web_publisher, '_readback_verify', lambda *args, **kwargs: platform_readback.ReadbackResult('unverified'))
    monkeypatch.setitem(web_publisher.PLATFORMS, platform, {
        **web_publisher.PLATFORMS[platform], 'publish_success': {'url_contains': '/p/'}})
    calendar = []
    monkeypatch.setitem(sys.modules, 'calendar_ops', SimpleNamespace(record_publish=lambda *args, **kwargs: calendar.append(args)))
    return args, timeline, recorder, calendar


def test_ui_only_submit_is_recorded_before_click_and_never_becomes_provider_ack(monkeypatch, tmp_path):
    args, timeline, recorder, calendar = browser_flow(monkeypatch, tmp_path)
    assert web_publisher.cmd_publish(args) == 0
    assert timeline.index(('receipt', 'submit_attempted')) < timeline.index(('click', 'zhihu'))
    assert all(event['phase'] != 'platform_ack' for event in recorder.events)
    assert any(event.get('reason') == 'ui_signal_only' for event in recorder.events)
    observed = next(event for event in recorder.events if event['phase'] == 'submit_attempted').get('account')
    assert observed == {'id': 'id:actual-self', 'authenticated': True}
    assert calendar == []


def test_uncertain_typed_click_is_not_repeated_using_js_fallback(monkeypatch, tmp_path):
    args, timeline, recorder, _ = browser_flow(monkeypatch, tmp_path, click_error=True)
    monkeypatch.setattr(web_publisher, '_resolve_steps', lambda *args: [
        {'action': 'js_click', 'selector': 'publish', 'commit': True}])
    with pytest.raises(RuntimeError, match='uncertain'):
        web_publisher.cmd_publish(args)
    assert timeline.count(('click', 'zhihu')) == 1
    assert not any(item[0] == 'fallback-click' for item in timeline)
    assert any(event['phase'] == 'submit_attempted' for event in recorder.events)


def test_receipt_write_failure_prevents_the_publication_click(monkeypatch, tmp_path):
    args, timeline, recorder, _ = browser_flow(monkeypatch, tmp_path)
    recorder.fail_attempt = True
    with pytest.raises(RuntimeError, match='receipt write'):
        web_publisher.cmd_publish(args)
    assert not any(item[0] == 'click' for item in timeline)


def test_kuaishou_legacy_readback_is_transported_without_inventing_ack_or_authenticated_author(monkeypatch, tmp_path):
    args, _, recorder, calendar = browser_flow(monkeypatch, tmp_path, 'kuaishou')
    # Supply an independent, synthetic account witness so this test exercises
    # readback transport separately from Kuaishou's missing bound identity probe.
    monkeypatch.setattr(web_publisher, '_account_binding', lambda *_: {
        'accountId': 'fixture-self', 'accountIdKind': 'uid', 'accountIdentityStatus': 'verified'})
    verdict = platform_readback.ReadbackResult('verified', platform_readback.WorkItem('post-one', 'Reviewed title', 'published'),
        evidence={'account': {'platform_account_id': 'possibly-work-author', 'display_name': 'Name'}})
    monkeypatch.setattr(web_publisher, '_readback_verify', lambda *args, **kwargs: verdict)
    assert web_publisher.cmd_publish(args) == 0
    received = next(event for event in recorder.events if event['phase'] == 'readback')
    assert received['readback'] == verdict.as_dict()
    assert all(event['phase'] != 'platform_ack' for event in recorder.events)
    assert {event['account']['id'] for event in recorder.events if event.get('account')} == {'uid:fixture-self'}
    assert calendar == []


def bili_flow(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('receipt_test_bili_upload', SCRIPTS.parents[1] / 'builtin/skill-bilibili-upload/scripts/bili_upload.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    timeline = []
    recorder = reporter_module(monkeypatch, Recorder(timeline))
    video = tmp_path / 'clip.mp4'; video.write_bytes(b'fixture-video')
    cookie = tmp_path / 'cookie.json'; cookie.write_text('{}')
    args = argparse.Namespace(video=str(video), title='Reviewed', partition='知识', tid=None, desc='', tag='', cover=None,
        copyright=1, source='', dtime=None, cookie=str(cookie), proxy=None, exec=True,
        receipt_context='context', receipt_file='receipt')
    monkeypatch.setattr(module, '_has_biliup', lambda: True)
    monkeypatch.setattr(module.content_guard, 'guard_or_die', lambda *args, **kwargs: None)
    monkeypatch.setattr(module.platform_readback, 'capture_bilibili_snapshot', lambda *_: {'old'})
    monkeypatch.setattr(module.platform_readback, 'read_bilibili_account', lambda *_: {'uid': 123, 'name': 'Actual account'})
    verdict = platform_readback.ReadbackResult('verified', platform_readback.WorkItem('BVfixture', 'Reviewed', '开放浏览'),
        evidence={'account': {'uid': 123, 'name': 'Actual account'}})
    monkeypatch.setattr(module.platform_readback, 'verify_bilibili_publish', lambda *args, **kwargs: verdict)
    def call(command, **kwargs):
        assert command[0] == 'biliup' and '--receipt-file' not in command and '--receipt-context' not in command
        timeline.append(('child', 'biliup')); return 0
    monkeypatch.setattr(module.subprocess, 'call', call)
    calendar = []
    monkeypatch.setitem(sys.modules, 'calendar_ops', SimpleNamespace(record_publish=lambda *args, **kwargs: calendar.append(args)))
    return module, args, timeline, recorder, calendar, verdict


def test_bili_receipt_precedes_child_and_zero_exit_is_not_provider_ack(monkeypatch, tmp_path):
    module, args, timeline, recorder, calendar, verdict = bili_flow(monkeypatch, tmp_path)
    assert module.cmd_upload(args) == 0
    assert timeline.index(('receipt', 'submit_attempted')) < timeline.index(('child', 'biliup'))
    assert next(event for event in recorder.events if event['phase'] == 'submit_attempted')['account'] == {
        'id': 'mid:123', 'authenticated': True}
    assert next(event for event in recorder.events if event['phase'] == 'readback')['readback'] == verdict.as_dict()
    assert all(event['phase'] != 'platform_ack' for event in recorder.events)
    assert calendar == []


def test_dry_run_creates_no_submission_receipt_or_child(monkeypatch, tmp_path):
    args, timeline, recorder, _ = browser_flow(monkeypatch, tmp_path)
    args.exec = False
    monkeypatch.setattr(web_publisher, 'cmd_plan', lambda *_: 0)
    assert web_publisher.cmd_publish(args) == 0
    assert recorder.events and all(event['phase'] == 'not_started' for event in recorder.events)
    assert recorder.events[-1]['reason'] == 'dry_run'
    assert not any(item[0] == 'click' for item in timeline)


def test_channels_records_before_enter_and_never_repeats_an_uncertain_submit(monkeypatch):
    timeline = []
    page = SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/create',
        goto=lambda *args, **kwargs: None, wait_for_timeout=lambda *_: None,
        wait_for_selector=lambda *args, **kwargs: None, wait_for_url=lambda *args, **kwargs: None,
        set_input_files=lambda *args, **kwargs: None)
    entry = SimpleNamespace(is_visible=lambda: True, click=lambda: None)
    page.locator = lambda *_: SimpleNamespace(count=lambda: 1, nth=lambda *_: entry)
    def frame_evaluate(script, *args):
        if 'document.activeElement === b' in script: return 'focused'
        if '!!document.querySelector' in script: return True
        return None
    page.frames = [SimpleNamespace(url='https://channels.weixin.qq.com/micro/content/post/create', evaluate=frame_evaluate)]
    page.keyboard = SimpleNamespace(press=lambda key: timeline.append(('enter', key)))
    monkeypatch.setattr(web_publisher, '_settle_login', lambda *_: None)
    monkeypatch.setattr(web_publisher, '_is_logged_in', lambda *_: True)
    with pytest.raises(RuntimeError, match='结果未确认'):
        web_publisher._publish_weixin_channels(page, {'media': 'fixture.mp4'},
            before_submit=lambda: timeline.append(('receipt', 'submit_attempted')))
    assert timeline == [('receipt', 'submit_attempted'), ('enter', 'Enter')]


def test_real_private_writer_keeps_ui_change_as_attempt_only(monkeypatch, tmp_path):
    args, timeline, _, calendar = browser_flow(monkeypatch, tmp_path)
    monkeypatch.delitem(sys.modules, 'publication_receipt')
    directory = tmp_path / 'host-private'; directory.mkdir(mode=0o700)
    context = {'operationId': 'a' * 32, 'payloadHash': 'b' * 64, 'platform': 'zhihu',
               'mode': 'publish_now', 'approvedAccountId': 'id:actual-self'}
    source = directory / 'context.json'; source.write_text(json.dumps(context)); source.chmod(0o600)
    output = directory / 'receipt.json'
    args.receipt_context = str(source); args.receipt_file = str(output)
    def click(**kwargs):
        assert json.loads(output.read_text())['phase'] == 'submit_attempted'
        timeline.append(('actual-click',))
    monkeypatch.setattr(web_publisher, '_first_visible_el', lambda *args: SimpleNamespace(
        click=click, scroll_into_view_if_needed=lambda: None))
    monkeypatch.setitem(web_publisher.PLATFORMS, 'zhihu', {**web_publisher.PLATFORMS['zhihu'], 'publish_success': {}})
    assert web_publisher.cmd_publish(args) == 0
    value = json.loads(output.read_text())
    assert value['phase'] == 'submit_attempted' and value['reason'] == 'ui_signal_only'
    assert value['account'] == {'id': 'id:actual-self', 'authenticated': True}
    assert 'ack' not in value
    assert output.stat().st_mode & 0o777 == 0o600
    assert calendar == []


def test_automatic_save_receipt_precedes_fill_without_blocking_final_submit(monkeypatch, tmp_path):
    args, timeline, recorder, _ = browser_flow(monkeypatch, tmp_path)
    monkeypatch.setattr(web_publisher, '_resolve_steps', lambda *args: [
        {'action': 'fill', 'selector': 'title', 'value': 'Reviewed'},
        {'action': 'click', 'selector': 'publish', 'commit': True}])
    monkeypatch.setattr(web_publisher, '_fill_first_visible', lambda *args: timeline.append(('fill',)))
    assert web_publisher.cmd_publish(args) == 0
    assert timeline.index(('receipt', 'submit_attempted')) < timeline.index(('fill',)) < timeline.index(('click', 'zhihu'))
    assert sum(item[0] == 'click' for item in timeline) == 1


def test_real_writer_wrong_account_stops_before_automatic_save_fill(monkeypatch, tmp_path):
    args, timeline, _, calendar = browser_flow(monkeypatch, tmp_path)
    monkeypatch.delitem(sys.modules, 'publication_receipt')
    directory = tmp_path / 'host-private'; directory.mkdir(mode=0o700)
    source = directory / 'context.json'
    source.write_text(json.dumps({'operationId': 'a' * 32, 'payloadHash': 'b' * 64, 'platform': 'zhihu',
        'mode': 'publish_now', 'approvedAccountId': 'id:different-approved-account'})); source.chmod(0o600)
    output = directory / 'receipt.json'
    args.receipt_context = str(source); args.receipt_file = str(output)
    monkeypatch.setattr(web_publisher, '_resolve_steps', lambda *args: [
        {'action': 'fill', 'selector': 'title', 'value': 'Reviewed'},
        {'action': 'click', 'selector': 'publish', 'commit': True}])
    monkeypatch.setattr(web_publisher, '_fill_first_visible', lambda *args: timeline.append(('fill',)))
    with pytest.raises(ValueError, match='receipt'):
        web_publisher.cmd_publish(args)
    assert not any(item[0] in ('fill', 'click') for item in timeline)
    assert json.loads(output.read_text())['phase'] == 'not_started'
    assert calendar == []


def bind_real_receipt(monkeypatch, tmp_path, args, account_id):
    monkeypatch.delitem(sys.modules, 'publication_receipt')
    directory = tmp_path / 'bound-receipt'
    directory.mkdir(mode=0o700)
    source = directory / 'context.json'
    source.write_text(json.dumps({'operationId': 'a' * 32, 'payloadHash': 'b' * 64,
        'platform': args.platform, 'mode': 'publish_now', 'approvedAccountId': account_id}))
    source.chmod(0o600)
    output = directory / 'receipt.json'
    args.receipt_context = str(source)
    args.receipt_file = str(output)
    return output


def test_kuaishou_bound_write_without_verified_identity_fails_closed(monkeypatch, tmp_path):
    args, timeline, _, _ = browser_flow(monkeypatch, tmp_path, 'kuaishou')
    receipt = bind_real_receipt(monkeypatch, tmp_path, args, 'uid:approved')
    with pytest.raises(ValueError, match='receipt'):
        web_publisher.cmd_publish(args)
    assert not any(event[0] in ('click', 'fallback-click') for event in timeline)
    assert json.loads(receipt.read_text())['phase'] == 'not_started'


def test_bound_zhihu_identity_probe_requests_no_cache(monkeypatch, tmp_path):
    authenticated = web_publisher._authenticated_account
    args, _, _, _ = browser_flow(monkeypatch, tmp_path)
    bind_real_receipt(monkeypatch, tmp_path, args, 'id:actual-self')
    monkeypatch.setattr(web_publisher, '_authenticated_account', authenticated)
    calls = []

    def get(url, **options):
        calls.append((url, options.get('headers', {})))
        return SimpleNamespace(status=200, json=lambda: {'id': 'actual-self', 'name': 'Me'})

    web_publisher._receipt_external_write(args, SimpleNamespace(request=SimpleNamespace(get=get)),
                                         web_publisher.PLATFORMS['zhihu'])
    assert calls == [('https://www.zhihu.com/api/v4/me', {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})]


@pytest.mark.parametrize('stage', ['first-fill', 'after-fill', 'final-click'])
def test_bound_zhihu_rechecks_identity_at_actual_write_boundaries(monkeypatch, tmp_path, stage):
    args, timeline, _, _ = browser_flow(monkeypatch, tmp_path)
    receipt = bind_real_receipt(monkeypatch, tmp_path, args, 'id:actual-self')
    identity = None if stage == 'first-fill' else {'id': 'actual-self', 'name': 'Me'}
    monkeypatch.setattr(web_publisher, '_authenticated_account', lambda *args, **kwargs: identity)

    def fill(*args):
        nonlocal identity
        timeline.append(('fill',))
        if stage == 'after-fill':
            identity = None

    def before_click(*args):
        nonlocal identity
        if stage == 'final-click':
            identity = None

    monkeypatch.setattr(web_publisher, '_fill_first_visible', fill)
    monkeypatch.setattr(web_publisher.human_pace, 'pause_before_commit', before_click)
    steps = [{'action': 'fill', 'selector': 'title', 'value': 'Reviewed'}]
    if stage == 'after-fill':
        steps.append({'action': 'fill', 'selector': 'body', 'value': 'Body'})
    steps.append({'action': 'click', 'selector': 'publish', 'commit': True, 'optional': True})
    monkeypatch.setattr(web_publisher, '_resolve_steps', lambda *args: steps)

    with pytest.raises(ValueError, match='receipt'):
        web_publisher.cmd_publish(args)
    assert timeline.count(('fill',)) == (0 if stage == 'first-fill' else 1)
    assert not any(event[0] == 'click' for event in timeline)
    assert json.loads(receipt.read_text())['phase'] == (
        'not_started' if stage == 'first-fill' else 'submit_attempted')


def test_final_callback_blocks_wrong_account_after_unknown_legacy_attempt(monkeypatch, tmp_path):
    args, timeline, _, _ = browser_flow(monkeypatch, tmp_path)
    receipt = bind_real_receipt(monkeypatch, tmp_path, args, 'id:approved')
    web_publisher._receipt_record(args, 'submit_attempted')
    with pytest.raises(ValueError, match='receipt'):
        web_publisher._receipt_submit(args, SimpleNamespace(), web_publisher.PLATFORMS['zhihu'])
        timeline.append(('click', 'zhihu'))
    assert not any(event[0] == 'click' for event in timeline)
    assert 'account' not in json.loads(receipt.read_text())


@pytest.mark.parametrize('changed', ['wrong-account', 'wrong-origin', 'service-worker'])
def test_channels_uses_fresh_auxiliary_response_before_its_callbacks(monkeypatch, tmp_path, changed):
    probe_events = []
    response = SimpleNamespace(url='https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/auth/auth_data',
        status=200, from_service_worker=False, json=lambda: {'errCode': 0, 'data': {
            'finderUser': {'finderUsername': 'actual-self', 'nickname': 'Me'}}})

    @contextlib.contextmanager
    def expect_response(predicate, *, timeout):
        assert 0 < timeout <= 10000
        result = SimpleNamespace()
        yield result
        if not predicate(response):
            raise TimeoutError('no trusted response')
        result.value = response

    probe = SimpleNamespace(expect_response=expect_response,
        route=lambda pattern, handler: probe_events.append(('route', pattern)),
        goto=lambda url, **kwargs: probe_events.append(('probe', url)),
        close=lambda: probe_events.append(('close',)))
    args, timeline, _, _ = browser_flow(monkeypatch, tmp_path, 'weixin-channels', probe=probe)
    bind_real_receipt(monkeypatch, tmp_path, args, 'finderUsername:actual-self')
    monkeypatch.setitem(web_publisher.PLATFORMS, 'weixin-channels',
                        {**web_publisher.PLATFORMS['weixin-channels'], 'publish_success': {}})

    def publish(page, ctx, *, before_write, before_submit):
        before_write()
        timeline.append(('upload',))
        if changed == 'wrong-origin':
            response.url = 'https://other.test/cgi-bin/mmfinderassistant-bin/auth/auth_data'
        elif changed == 'service-worker':
            response.from_service_worker = True
        else:
            response.json = lambda: {'errCode': 0, 'data': {
                'finderUser': {'finderUsername': 'different', 'nickname': 'Other'}}}
        before_submit()
        timeline.append(('enter',))

    monkeypatch.setattr(web_publisher, '_publish_weixin_channels', publish)
    with pytest.raises(ValueError, match='receipt'):
        web_publisher.cmd_publish(args)
    assert ('upload',) in timeline
    assert ('enter',) not in timeline
    assert probe_events == [
        ('route', 'https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/auth/auth_data*'),
        ('probe', 'https://channels.weixin.qq.com'), ('close',),
    ] * 2


@pytest.mark.parametrize('failure', ['nonzero', 'timeout', 'readback_error'])
def test_bili_failure_retains_single_attempt_and_never_resends(monkeypatch, tmp_path, failure):
    module, args, timeline, recorder, calendar, _ = bili_flow(monkeypatch, tmp_path)
    def child(command, **kwargs):
        timeline.append(('child', 'biliup'))
        if failure == 'timeout': raise subprocess.TimeoutExpired(command, 1)
        return 7 if failure == 'nonzero' else 0
    monkeypatch.setattr(module.subprocess, 'call', child)
    if failure == 'readback_error':
        monkeypatch.setattr(module.platform_readback, 'verify_bilibili_publish',
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('fixture readback error')))
    if failure == 'nonzero':
        assert module.cmd_upload(args) == 7
    elif failure == 'timeout':
        with pytest.raises(subprocess.TimeoutExpired): module.cmd_upload(args)
    else:
        with pytest.raises(SystemExit) as caught: module.cmd_upload(args)
        assert caught.value.code == 4
    assert timeline.count(('child', 'biliup')) == 1
    assert any(event['phase'] == 'submit_attempted' for event in recorder.events)
    assert not any(event['phase'] == 'platform_ack' for event in recorder.events)
    assert calendar == []


def test_no_flags_cli_also_avoids_false_published_calendar_rows(monkeypatch, tmp_path):
    args, _, recorder, calendar = browser_flow(monkeypatch, tmp_path)
    recorder.enabled = False
    args.receipt_context = args.receipt_file = None
    assert web_publisher.cmd_publish(args) == 0
    assert calendar == []


def test_web_cli_accepts_receipt_flags_without_forwarding_them_to_browser_steps(monkeypatch):
    observed = []
    monkeypatch.setattr(web_publisher, 'cmd_publish', lambda args: observed.append(args) or 0)
    monkeypatch.setattr(sys, 'argv', ['web_publisher.py', 'publish', '--platform', 'zhihu',
        '--receipt-context', '/private/context.json', '--receipt-file', '/private/receipt.json', '--exec'])
    assert web_publisher.main() == 0
    assert observed[0].receipt_context == '/private/context.json'
    assert observed[0].receipt_file == '/private/receipt.json'


def test_bili_cli_accepts_receipt_flags_and_preserves_native_command_flags(monkeypatch):
    spec = importlib.util.spec_from_file_location('receipt_test_bili_cli', SCRIPTS.parents[1] / 'builtin/skill-bilibili-upload/scripts/bili_upload.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    observed = []
    monkeypatch.setattr(module, 'cmd_upload', lambda args: observed.append(args) or 0)
    monkeypatch.setattr(sys, 'argv', ['bili_upload.py', 'upload', '--video', 'video.mp4', '--title', 'Title',
        '--cookie', 'cookies.json', '--tid', '36', '--copyright', '1', '--exec',
        '--receipt-context', '/private/context.json', '--receipt-file', '/private/receipt.json'])
    assert module.main() == 0
    assert observed[0].receipt_context == '/private/context.json'
    assert observed[0].receipt_file == '/private/receipt.json'
    command = module.build_cmd(observed[0])
    assert command[:5] == ['biliup', '-u', 'cookies.json', 'upload', 'video.mp4']
    assert '--receipt-context' not in command and '--receipt-file' not in command


def test_bili_bound_upload_uses_only_the_credential_snapshot_verified_for_the_write(monkeypatch, tmp_path):
    module, args, _, _, _, _ = bili_flow(monkeypatch, tmp_path)
    original = Path(args.cookie)
    original.write_bytes(b'fixture-approved-cookie')
    verified_paths = []

    def observe(cookie):
        pinned = Path(cookie)
        assert pinned != original
        assert pinned.read_bytes() == b'fixture-approved-cookie'
        assert pinned.stat().st_mode & 0o777 == 0o600
        original.write_bytes(b'fixture-replacement-cookie')
        verified_paths.append(pinned)
        return {'uid': 123}

    def submit(command, **kwargs):
        assert Path(command[2]) == verified_paths[0]
        assert Path(command[2]).read_bytes() == b'fixture-approved-cookie'
        return 0

    monkeypatch.setattr(module.platform_readback, 'read_bilibili_account', observe)
    monkeypatch.setattr(module.subprocess, 'call', submit)
    assert module.cmd_upload(args) == 0
    assert len(verified_paths) == 1 and not verified_paths[0].exists()


def test_bili_missing_authenticated_account_cannot_spawn_bound_upload(monkeypatch, tmp_path):
    module, args, timeline, recorder, _, _ = bili_flow(monkeypatch, tmp_path)
    monkeypatch.setattr(module.platform_readback, 'read_bilibili_account', lambda *_: None)
    with pytest.raises(ValueError):
        module.cmd_upload(args)
    assert ('child', 'biliup') not in timeline
    assert all(event['phase'] == 'not_started' for event in recorder.events)


def test_bili_bound_upload_rejects_a_verified_account_other_than_the_approval(monkeypatch, tmp_path):
    module, args, timeline, _, _, _ = bili_flow(monkeypatch, tmp_path)
    args.platform = 'bilibili'
    output = bind_real_receipt(monkeypatch, tmp_path, args, 'mid:456')
    with pytest.raises(ValueError, match='receipt'):
        module.cmd_upload(args)
    assert ('child', 'biliup') not in timeline
    assert json.loads(output.read_text())['phase'] == 'not_started'
