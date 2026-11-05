"""Actual CLI entry flows with finite fake pages; no browser/process/network may escape."""
from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'shared' / 'scripts'


class PWTimeout(Exception):
    pass


class PWError(Exception):
    pass


class Reporter:
    def __init__(self, enabled, events):
        self.enabled = enabled
        self.records = []
        self.events = events

    def require_approved_account(self, account):
        if not account or account.get('authenticated') is not True:
            raise ValueError('Missing authenticated account')
        self.events.append(('approved-account', account['id']))

    def record(self, phase, account=None, ack=None, readback=None, reason=None):
        if self.enabled:
            item = dict(phase=phase, account=account, ack=ack, readback=readback, reason=reason)
            self.records.append(item)
            self.events.append(('receipt', phase))

    def mark_submission_ambiguous(self):
        self.events.append(('ambiguous-submission',))


@pytest.fixture
def harness(monkeypatch, tmp_path):
    def forbidden(*_a, **_kw):
        raise AssertionError('REAL NETWORK/PROCESS IS FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    events = []
    reporters = []
    factories = []
    writer = ModuleType('publication_receipt')
    class Factory:
        @classmethod
        def from_args(cls, args, platform):
            factories.append((args, platform))
            value = Reporter(bool(getattr(args, 'receipt_context', None) or getattr(args, 'receipt_file', None)), events)
            reporters.append(value)
            return value
    writer.PublicationReceiptReporter = Factory
    monkeypatch.setitem(sys.modules, 'publication_receipt', writer)
    fake_pw = ModuleType('playwright.sync_api')
    class Manager:
        def __enter__(self):
            events.append(('browser-manager',)); return SimpleNamespace()
        def __exit__(self, *_args):
            return False
    fake_pw.sync_playwright = Manager
    fake_pw.TimeoutError = PWTimeout
    fake_pw.Error = PWError
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', fake_pw)
    calendar = ModuleType('calendar_ops'); calendar.record_publish = Mock()
    monkeypatch.setitem(sys.modules, 'calendar_ops', calendar)
    def load(name):
        spec = importlib.util.spec_from_file_location(f'receipt_adapter_{name}', SCRIPTS / f'{name}.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module
    image = tmp_path / 'cover.jpg'; image.write_bytes(b'fixture-image')
    video = tmp_path / 'clip.mp4'; video.write_bytes(b'fixture-video')
    html = tmp_path / 'article.html'; html.write_text('<p>test article</p>', encoding='utf-8')
    def args(**overrides):
        return SimpleNamespace(title='安全测试标题', content='普通正文', tags='教育', images=str(image), video=str(video),
            exec=True, allow_unsafe=False, headed=False, keep_open=False, profile_base=str(tmp_path / 'profile'),
            proxy=None, no_proxy=True, status_file=None, sms_code_file=None,
            html=str(html), cover=str(image), digest='', author='', source_url='',
            receipt_context=str(tmp_path / 'receipt-context.json'), receipt_file=str(tmp_path / 'receipt.json'), **overrides)
    return SimpleNamespace(load=load, events=events, reporters=reporters, factories=factories,
        calendar=calendar, args=args, tmp_path=tmp_path, monkeypatch=monkeypatch)


class Element:
    def __init__(self, events, failure=None): self.events = events; self.failure = failure
    def click(self, **_kwargs):
        self.events.append(('submit-click',))
        if self.failure: raise self.failure
    def scroll_into_view_if_needed(self): pass
    def bounding_box(self): return None


def fake_context(page):
    return SimpleNamespace(pages=[page], new_page=lambda: page, close=lambda: None)


def xhs(h, *, account=None, upload_failure=None, click_failure=None):
    module = h.load('xhs_publish')
    page = SimpleNamespace(url=module.PUBLISH_URL, goto=lambda *_a, **_k: None,
        set_default_timeout=lambda *_a: None, wait_for_timeout=lambda *_a: None,
        query_selector=lambda _selector: SimpleNamespace(click=lambda: None))
    h.monkeypatch.setattr(module, '_launch', lambda *_a, **_k: fake_context(page))
    h.monkeypatch.setattr(module, '_ProfileLock', lambda _path: SimpleNamespace(acquire=lambda *_a: None, release=lambda: None))
    h.monkeypatch.setattr(module, '_authenticated_account', lambda _context: account)
    h.monkeypatch.setattr(module, '_creator_account', lambda _context: account)
    for name in ('_click_publish_tab', '_upload_video', '_human_type', '_input_tags', '_check_overflow', '_confirm_publish_dialog', '_wait_publish_success'):
        h.monkeypatch.setattr(module, name, lambda *_a, **_k: None)
    def upload(*_a):
        h.events.append(('upload-media',))
        if upload_failure: raise upload_failure
    h.monkeypatch.setattr(module, '_upload_images', upload)
    h.monkeypatch.setattr(module, '_content_element', lambda _page: object())
    def ready(*_a):
        h.events.append(('button-ready',)); return 'old', Element(h.events, click_failure)
    h.monkeypatch.setattr(module, '_wait_publish_clickable', ready)
    return module


def _douyin_self(uid='observed-dy'):
    return {'httpStatus': 200, 'body': {'status_code': 0, 'user_info': {'uid': uid}}}


def douyin(h, result, *, response=_douyin_self(), click_failure=None, stay_in_editor=False):
    module = h.load('douyin_publish')
    def evaluate(_script, request):
        if request[0].startswith(module.platform_readback.DOUYIN_WORK_LIST_URL + '?'):
            h.events.append(('baseline-request', tuple(request)))
            return {'httpStatus': 200, 'body': {'status_code': 0, 'data': {'work_list': []}}}
        h.events.append(('account-probe',))
        h.events.append(('account-request', tuple(request)))
        return response() if callable(response) else response
    page = SimpleNamespace(url='https://creator.douyin.com/creator-micro/content/post/video' if stay_in_editor else module.HOME_URL,
        goto=lambda *_a, **_k: None,
        set_default_timeout=lambda *_a: None, wait_for_timeout=lambda *_a: None,
        evaluate=evaluate)
    h.monkeypatch.setattr(module, '_launch', lambda *_a, **_k: fake_context(page))
    for name in ('_wait_ready', '_go_upload', '_switch_tab', '_wait_video_processed', '_wait_toast', '_dump_publish_fail'):
        h.monkeypatch.setattr(module, name, lambda *_a, **_k: None)
    h.monkeypatch.setattr(module, '_upload_files', lambda *_a: h.events.append(('upload-media',)))
    h.monkeypatch.setattr(module, '_select_ai_cover', lambda *_a: h.events.append(('select-cover',)))
    h.monkeypatch.setattr(module, '_fill_title_desc', lambda *_a: h.events.append(('fill-editor',)))
    h.monkeypatch.setattr(module, '_logged_in', lambda _page: True)
    h.monkeypatch.setattr(module, '_verify_wall', lambda _page: False)
    h.monkeypatch.setattr(module.human_pace, 'pace', lambda *_a: None)
    h.monkeypatch.setattr(module.human_pace, 'pause_before_commit', lambda *_a: None)
    h.monkeypatch.setattr(module, '_wait_publish_button_ready', lambda *_a: (h.events.append(('button-ready',)) or Element(h.events, click_failure)))
    h.monkeypatch.setattr(module.platform_readback, 'capture_douyin_snapshot', lambda _page: {'older-work'})
    h.monkeypatch.setattr(module.platform_readback, 'verify_douyin_publish', lambda *_a, **_k: result)
    h.monkeypatch.setattr(module, '_readback_verify', lambda *_a, **_k: result)
    return module


def mp(h, *, draft=None, cover=None, failure=None):
    module = h.load('weixin_mp_stats')
    cover = cover or {'base_resp': {'ret': 0}, 'content': {'media_id': 'cover-only-id'}}
    draft = draft if draft is not None else {'ret': 0, 'appMsgId': 'draft-real-id'}
    def post(url, **kwargs):
        if 'filetransfer?' in url:
            h.events.append(('provider-post', 'cover'))
            if failure: raise failure
            return SimpleNamespace(json=lambda: cover)
        assert 'operate_appmsg?' in url, 'unknown provider request'
        h.events.append(('provider-post', 'draft'))
        return SimpleNamespace(json=lambda: draft, text=lambda: 'fixture')
    page = SimpleNamespace(url=module.MP_HOME + '?token=12345', goto=lambda *_a, **_k: None,
        wait_for_timeout=lambda *_a: None, request=SimpleNamespace(post=post))
    h.monkeypatch.setattr(module, '_launch', lambda *_a, **_k: fake_context(page))
    h.monkeypatch.setattr(module, '_editor_ctx', lambda *_a: {'ticket': 'fixture-ticket', 'user_name': 'not-a-verified-uid'})
    return module


@pytest.mark.parametrize('name', ['xhs_publish', 'douyin_publish'])
def test_dry_run_records_not_started_without_browser(harness, name):
    module = harness.load(name); args = harness.args(); args.exec = False
    assert module.cmd_publish(args) == 0
    assert harness.reporters and harness.reporters[-1].records[-1]['phase'] == 'not_started'
    assert harness.reporters[-1].records[-1]['reason'] == 'dry_run'
    assert ('browser-manager',) not in harness.events
    harness.calendar.record_publish.assert_not_called()


def test_xhs_ui_signal_is_attempt_only_with_observed_account_and_no_calendar(harness):
    module = xhs(harness, account={'userId': 'observed-xhs', 'userName': 'self'})
    assert module.cmd_publish(harness.args()) == 0
    records = harness.reporters[-1].records
    assert records[-1]['phase'] == 'submit_attempted' and records[-1]['reason'] == 'ui_signal_only'
    assert records[-1]['account'] == {'id': 'userId:observed-xhs', 'authenticated': True}
    assert all(item['ack'] is None for item in records)
    assert harness.events.index(('receipt', 'submit_attempted')) < harness.events.index(('upload-media',))
    assert harness.events.index(('button-ready',)) < harness.events.index(('submit-click',))
    harness.calendar.record_publish.assert_not_called()


def test_xhs_upload_timeout_keeps_attempt_without_click_or_ack(harness):
    module = xhs(harness, account={'userId': 'observed-xhs'}, upload_failure=PWTimeout('upload timeout'))
    with pytest.raises(SystemExit): module.cmd_publish(harness.args())
    assert harness.reporters[-1].records[-1]['phase'] == 'submit_attempted'
    assert not any(item['ack'] for item in harness.reporters[-1].records)
    assert ('submit-click',) not in harness.events


def test_douyin_legacy_verified_is_full_readback_observation_not_public_success(harness):
    sys.path.insert(0, str(SCRIPTS)); import platform_readback
    observation = platform_readback.ReadbackResult(outcome='verified',
        matched=platform_readback.WorkItem('candidate-id', '安全测试标题', '审核中', 123456),
        candidates=[platform_readback.WorkItem('candidate-id', '安全测试标题', '审核中', 123456)],
        evidence={'account': {'platform_account_id': 'untyped-id', 'display_name': 'account'}})
    module = douyin(harness, observation)
    assert module.cmd_publish_video(harness.args()) == 0
    records = harness.reporters[-1].records
    assert records[-1]['phase'] == 'readback' and records[-1]['readback'] == observation.as_dict()
    assert records[-1]['account'] == {'id': 'uid:observed-dy', 'authenticated': True}
    harness.calendar.record_publish.assert_not_called()


def test_mp_only_real_draft_ack_uses_provider_media_id_and_unknown_account(harness):
    module = mp(harness)
    assert module.cmd_publish(harness.args()) == 0
    last = harness.reporters[-1].records[-1]
    assert last['phase'] == 'platform_ack'
    assert last['account'] is None
    assert last['ack'] == {'kind': 'draft', 'accepted': True, 'id': 'draft-real-id', 'source': 'provider_response'}
    assert harness.events.index(('receipt', 'submit_attempted')) < harness.events.index(('provider-post', 'cover'))


def test_mp_cover_id_alone_cannot_be_a_draft_receipt(harness):
    module = mp(harness, draft={'ret': 0})
    assert module.cmd_publish(harness.args()) == 1
    assert harness.reporters[-1].records[-1]['phase'] == 'submit_attempted'
    assert not any(item['ack'] for item in harness.reporters[-1].records)


def test_xhs_ui_navigation_stdout_does_not_claim_publication(harness, capsys):
    module = harness.load('xhs_publish')
    module._wait_publish_success(SimpleNamespace(url='https://creator.xiaohongshu.com/new/manage'), 1)
    output = capsys.readouterr().out
    assert '发布成功' not in output
    assert '界面' in output and '未核实' in output


def test_xhs_error_after_click_never_returns_to_not_started(harness):
    module = xhs(harness, account={'userId': 'observed-xhs'}, click_failure=PWTimeout('after click'))
    with pytest.raises(SystemExit): module.cmd_publish(harness.args())
    phases = [item['phase'] for item in harness.reporters[-1].records]
    assert 'submit_attempted' in phases
    assert 'not_started' not in phases[phases.index('submit_attempted'):]
    harness.calendar.record_publish.assert_not_called()


def test_douyin_authenticated_pre_submit_identity_is_namespaced_not_borrowed_from_context(harness):
    sys.path.insert(0, str(SCRIPTS)); import platform_readback
    result = platform_readback.ReadbackResult('verified', matched=platform_readback.WorkItem('new-id', '安全测试标题', 'published'))
    response = {'httpStatus': 200, 'body': {'status_code': 0, 'user_info': {'uid': 'observed-uid', 'nickname': 'self'}}}
    module = douyin(harness, result, response=response)
    assert module.cmd_publish_video(harness.args()) == 0
    assert harness.reporters[-1].records[-1]['account'] == {'id': 'uid:observed-uid', 'authenticated': True}
    attempts = [i for i, event in enumerate(harness.events) if event == ('receipt', 'submit_attempted')]
    assert attempts[0] < harness.events.index(('upload-media',))
    assert harness.events.index(('button-ready',)) < attempts[1] < harness.events.index(('submit-click',))
    probes = [event[1] for event in harness.events if event[0] == 'account-request']
    assert probes == [(module.platform_readback.DOUYIN_PROFILE_API, 4000, 'no-store')] * 3


def test_douyin_timeout_after_click_keeps_attempt_and_full_failed_readback(harness):
    sys.path.insert(0, str(SCRIPTS)); import platform_readback
    result = platform_readback.ReadbackResult('readback_error', error='offline verification failed')
    module = douyin(harness, result, click_failure=PWTimeout('clicked then timeout'))
    with pytest.raises(SystemExit): module.cmd_publish_video(harness.args())
    records = harness.reporters[-1].records
    assert records[-1]['phase'] == 'readback' and records[-1]['readback'] == result.as_dict()
    phases = [item['phase'] for item in records]
    assert 'not_started' not in phases[phases.index('submit_attempted'):]


@pytest.mark.parametrize('name', ['xhs_publish', 'douyin_publish'])
def test_no_receipt_flags_still_do_not_auto_record_weak_evidence_as_published(harness, name, capsys):
    if name == 'xhs_publish': module = xhs(harness)
    else:
        sys.path.insert(0, str(SCRIPTS)); import platform_readback
        module = douyin(harness, platform_readback.ReadbackResult('verified', matched=platform_readback.WorkItem('candidate', '安全测试标题', '审核中')))
    args = harness.args(); args.receipt_context = None; args.receipt_file = None
    assert module.cmd_publish(args) == 0
    harness.calendar.record_publish.assert_not_called()
    assert '发布成功' not in capsys.readouterr().out


def test_mp_timeout_after_material_post_keeps_attempt_without_draft_ack(harness):
    module = mp(harness, failure=PWTimeout('cover response lost'))
    with pytest.raises(PWTimeout): module.cmd_publish(harness.args())
    records = harness.reporters[-1].records
    assert records[-1]['phase'] == 'submit_attempted'
    assert not any(item['ack'] for item in records)
    assert 'not_started' not in [item['phase'] for item in records][1:]


@pytest.mark.parametrize('name', ['xhs_publish', 'douyin_publish', 'weixin_mp_stats'])
def test_optional_receipt_flags_reach_the_original_cli_publish_command(harness, name):
    module = mp(harness) if name == 'weixin_mp_stats' else harness.load(name)
    args = harness.args()
    command = [name, 'publish', '--title', args.title, '--no-proxy', '--receipt-context', args.receipt_context, '--receipt-file', args.receipt_file]
    if name == 'weixin_mp_stats': command += ['--html', args.html, '--cover', args.cover]
    else: command += ['--images', args.images]
    harness.monkeypatch.setattr(sys, 'argv', command)
    assert module.main() == 0
    parsed, platform = harness.factories[-1]
    assert parsed.receipt_context == args.receipt_context and parsed.receipt_file == args.receipt_file
    assert platform == {'xhs_publish': 'xiaohongshu', 'douyin_publish': 'douyin', 'weixin_mp_stats': 'wechat-oa'}[name]


def real_writer(h, platform, account):
    import json
    spec = importlib.util.spec_from_file_location('actual_publication_receipt_writer', SCRIPTS / 'publication_receipt.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    h.monkeypatch.setitem(sys.modules, 'publication_receipt', module)
    args = h.args()
    context = Path(args.receipt_context).resolve()
    context.write_text(json.dumps({'operationId': '1' * 32, 'payloadHash': '2' * 64,
        'platform': platform, 'mode': 'create_draft' if platform == 'wechat-oa' else 'publish_now',
        'approvedAccountId': account}), encoding='utf-8')
    context.chmod(0o600)
    args.receipt_context = str(context); args.receipt_file = str(Path(args.receipt_file).resolve())
    return args, module


@pytest.mark.parametrize('platform', ['xiaohongshu', 'douyin', 'wechat-oa'])
def test_real_shared_writer_emits_correlated_envelope_from_each_actual_adapter(harness, platform):
    import json
    if platform == 'xiaohongshu':
        module = xhs(harness, account={'userId': 'observed-xhs'})
        args, _ = real_writer(harness, platform, 'userId:observed-xhs')
        assert module.cmd_publish(args) == 0
    elif platform == 'douyin':
        sys.path.insert(0, str(SCRIPTS)); import platform_readback
        result = platform_readback.ReadbackResult('verified', matched=platform_readback.WorkItem('native-post', '安全测试标题', 'published'))
        response = {'httpStatus': 200, 'body': {'status_code': 0, 'user_info': {'uid': 'observed-dy'}}}
        module = douyin(harness, result, response=response)
        args, _ = real_writer(harness, platform, 'uid:observed-dy')
        assert module.cmd_publish_video(args) == 0
    else:
        module = mp(harness)
        args, _ = real_writer(harness, platform, 'user_name:approved-but-unobserved')
        assert module.cmd_publish(args) == 0
    value = json.loads(Path(args.receipt_file).read_text(encoding='utf-8'))
    assert value['kind'] == 'publication_receipt' and value['schemaVersion'] == 1
    assert value['operationId'] == '1' * 32 and value['payloadHash'] == '2' * 64 and value['platform'] == platform
    if platform == 'xiaohongshu':
        assert value['phase'] == 'submit_attempted' and value['reason'] == 'ui_signal_only' and 'ack' not in value
    elif platform == 'douyin':
        assert value['phase'] == 'readback' and value['readback']['matched']['platform_content_id'] == 'native-post'
        assert value['account'] == {'id': 'uid:observed-dy', 'authenticated': True}
    else:
        assert value['phase'] == 'platform_ack' and value['ack']['id'] == 'draft-real-id' and value['ack']['kind'] == 'draft'
        assert value.get('account') is None
    harness.calendar.record_publish.assert_not_called()


def test_wrong_authenticated_account_is_stopped_by_real_writer_before_any_submit(harness):
    import json
    module = xhs(harness, account={'userId': 'different-account'})
    args, writer = real_writer(harness, 'xiaohongshu', 'userId:approved-account')
    with pytest.raises(writer.PublicationReceiptError): module.cmd_publish(args)
    assert ('submit-click',) not in harness.events
    assert ('upload-media',) not in harness.events
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'not_started'


def test_mp_missing_input_stays_not_started_before_browser(harness):
    module = mp(harness); args = harness.args(); args.html = str(harness.tmp_path / 'missing.html')
    with pytest.raises(FileNotFoundError): module.cmd_publish(args)
    assert harness.reporters[-1].records[-1]['phase'] == 'not_started'
    assert ('browser-manager',) not in harness.events


@pytest.mark.parametrize('invalid_id', [True, {'id': 'not-an-id'}, 'https://provider.test/success', '发布成功'])
def test_mp_malformed_provider_id_cannot_be_coerced_into_a_draft_ack(harness, invalid_id):
    module = mp(harness, draft={'ret': 0, 'appMsgId': invalid_id})
    assert module.cmd_publish(harness.args()) == 1
    assert harness.reporters[-1].records[-1]['phase'] == 'submit_attempted'
    assert not any(item['ack'] for item in harness.reporters[-1].records)


@pytest.mark.parametrize('observed', [None, _douyin_self('other-account')])
def test_douyin_missing_or_wrong_account_blocks_upload_and_click(harness, observed):
    import json
    module = douyin(harness, None, response=observed)
    args, writer = real_writer(harness, 'douyin', 'uid:observed-dy')
    with pytest.raises(writer.PublicationReceiptError):
        module.cmd_publish_video(args)
    assert ('upload-media',) not in harness.events
    assert ('submit-click',) not in harness.events
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'not_started'


def test_douyin_account_switch_after_upload_blocks_editor_writes(harness):
    import json
    readings = iter([_douyin_self(), _douyin_self('other-account')])
    module = douyin(harness, None, response=lambda: next(readings))
    args, writer = real_writer(harness, 'douyin', 'uid:observed-dy')
    with pytest.raises(writer.PublicationReceiptError):
        module.cmd_publish_video(args)
    assert harness.events.count(('account-probe',)) == 2
    assert harness.events.count(('upload-media',)) == 1
    assert ('select-cover',) not in harness.events
    assert ('fill-editor',) not in harness.events
    assert ('submit-click',) not in harness.events
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'submit_attempted'


def test_douyin_bound_submit_is_not_clicked_again_if_editor_does_not_navigate(harness):
    import platform_readback
    result = platform_readback.ReadbackResult('verified', matched=platform_readback.WorkItem('candidate', '安全测试标题', '审核中'))
    module = douyin(harness, result, stay_in_editor=True)
    args, _ = real_writer(harness, 'douyin', 'uid:observed-dy')
    assert module.cmd_publish_video(args) == 0
    assert harness.events.count(('submit-click',)) == 1
    assert harness.events.count(('account-probe',)) == 3


def test_douyin_bound_sms_click_failure_does_not_try_second_candidate(harness):
    module = harness.load('douyin_publish')
    clicks = []
    def uncertain(**_kwargs):
        clicks.append('first')
        raise PWTimeout('click may have landed')
    first = SimpleNamespace(is_visible=lambda: True, query_selector_all=lambda _sel: [], click=uncertain)
    second = SimpleNamespace(is_visible=lambda: True, query_selector_all=lambda _sel: [], click=lambda **_kw: clicks.append('second'))
    modal = SimpleNamespace(query_selector_all=lambda _sel: [first, second])
    harness.monkeypatch.setattr(module, '_sms_modal', lambda _page: modal)
    approved = Mock()
    with pytest.raises(PWTimeout):
        module._sms_click_submit(object(), before_verify=approved)
    approved.assert_called_once_with()
    assert clicks == ['first']


def test_douyin_bound_sms_path_runs_once_when_the_wall_reappears(harness):
    import platform_readback
    result = platform_readback.ReadbackResult('verified', matched=platform_readback.WorkItem('candidate', '安全测试标题', '审核中'))
    module = douyin(harness, result, stay_in_editor=True)
    harness.monkeypatch.setattr(module, '_verify_wall', lambda _page: True)
    verification = Mock(return_value=True)
    harness.monkeypatch.setattr(module, '_handle_publish_sms', verification)
    args, _ = real_writer(harness, 'douyin', 'uid:observed-dy')
    assert module.cmd_publish_video(args) == 0
    verification.assert_called_once()
    assert callable(verification.call_args.kwargs['before_verify'])
    assert harness.events.count(('submit-click',)) == 1


def test_douyin_unbound_cli_preserves_editor_fallback(harness):
    import platform_readback
    result = platform_readback.ReadbackResult('verified', matched=platform_readback.WorkItem('candidate', '安全测试标题', '审核中'))
    module = douyin(harness, result, response=None, stay_in_editor=True)
    args = harness.args()
    args.receipt_context = None
    args.receipt_file = None
    assert module.cmd_publish_video(args) == 0
    assert harness.events.count(('submit-click',)) == 2
    assert ('account-probe',) not in harness.events


def test_douyin_unbound_timeout_before_submit_preserves_readback(harness):
    module = douyin(harness, None, response=None)
    args = harness.args()
    args.receipt_context = None
    args.receipt_file = None
    upload = Mock(side_effect=PWTimeout('upload timeout'))
    readback = Mock(return_value=None)
    harness.monkeypatch.setattr(module, '_upload_files', upload)
    harness.monkeypatch.setattr(module, '_readback_verify', readback)
    with pytest.raises(SystemExit):
        module.cmd_publish_video(args)
    readback.assert_called_once()
    assert ('submit-click',) not in harness.events


def test_douyin_rechecks_again_after_editor_writes_before_publish(harness):
    readings = iter([_douyin_self(), _douyin_self(), _douyin_self('other-account')])
    module = douyin(harness, None, response=lambda: next(readings))
    args, writer = real_writer(harness, 'douyin', 'uid:observed-dy')
    with pytest.raises(writer.PublicationReceiptError):
        module.cmd_publish_video(args)
    assert harness.events.count(('account-probe',)) == 3
    assert harness.events.count(('fill-editor',)) == 1
    assert ('submit-click',) not in harness.events
