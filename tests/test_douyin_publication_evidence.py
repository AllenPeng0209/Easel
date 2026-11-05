"""Douyin response evidence is bound to one UI submit; all pages are finite fakes."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.test_publisher_receipt_adapters import harness as _adapter_harness, douyin, real_writer

CREATE = 'https://creator.douyin.com/web/api/media/aweme/create_v2/?signed=fixture'
ACCOUNT = {'id': 'uid:current-owner', 'authenticated': True}
ID = '7612345678901234567'


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return _adapter_harness.__wrapped__(monkeypatch, tmp_path)


class Page:
    def __init__(self):
        self.listeners = {}

    def on(self, kind, callback):
        self.listeners[kind] = callback

    def remove_listener(self, kind, callback):
        if self.listeners.get(kind) is callback:
            self.listeners.pop(kind)

    def emit(self, kind, value):
        callback = self.listeners.get(kind)
        if callback:
            callback(value)


def request(**overrides):
    return SimpleNamespace(url=CREATE, method='POST', post_data_json={'item': {'common': {
        'text': '猫咪打工日记 先伸懒腰。 #猫咪', 'media_type': 4, 'visibility_type': 0,
        'timing': 0, 'creation_id': 'one-creation', 'video_id': 'uploaded-video',
    }}}, **overrides)


def response(req, body=None, status=200):
    return SimpleNamespace(request=req, url=req.url, status=status,
        json=lambda: {'status_code': 0, 'item_id': ID} if body is None else body)


def capture(module, page):
    assert hasattr(module, '_DouyinSubmitAck'), 'Missing exact UI submission ACK observer'
    observed = []
    result = module._DouyinSubmitAck(page, title='猫咪打工日记', desc='先伸懒腰。', kind='video',
        on_ack=lambda account, ack: observed.append((account, ack)))
    return result, observed


def test_only_the_same_armed_submit_request_can_supply_a_provider_ack(harness):
    module = harness.load('douyin_publish'); page = Page(); observer, observed = capture(module, page)
    old = request(); page.emit('request', old); page.emit('response', response(old))
    assert observer.ack is None and observed == []
    observer.arm(ACCOUNT)
    real = request(); page.emit('request', real)
    page.emit('response', response(request()))
    assert observer.ack is None
    page.emit('response', response(real))
    assert observer.ack == {'kind': 'post', 'accepted': True, 'id': ID, 'source': 'provider_response', 'state': 'submitted'}
    assert observed == [(ACCOUNT, observer.ack)]
    observer.close(); assert page.listeners == {}


@pytest.mark.parametrize('change', ['foreign-host', 'get', 'upload', 'wrong-copy', 'failed', 'missing-id', 'zero-id', 'bool-status'])
def test_unrelated_or_ambiguous_provider_responses_never_create_an_ack(harness, change):
    module = harness.load('douyin_publish'); page = Page(); observer, observed = capture(module, page); observer.arm(ACCOUNT)
    req = request(); body = {'status_code': 0, 'item_id': ID}
    if change == 'foreign-host': req.url = CREATE.replace('creator.douyin.com', 'creator.douyin.com.attacker.test')
    if change == 'get': req.method = 'GET'
    if change == 'upload': req.url = CREATE.replace('/aweme/create_v2/', '/upload/auth/v5/')
    if change == 'wrong-copy': req.post_data_json['item']['common']['text'] = 'another post'
    if change == 'failed': body['status_code'] = 1
    if change == 'missing-id': body.pop('item_id')
    if change == 'zero-id': body['item_id'] = '0'
    if change == 'bool-status': body['status_code'] = False
    page.emit('request', req); page.emit('response', response(req, body))
    assert observer.ack is None and observed == []


def test_two_distinct_create_requests_are_ambiguous_and_never_produce_public_readback_authority(harness):
    module = harness.load('douyin_publish'); page = Page(); observer, _ = capture(module, page); observer.arm(ACCOUNT)
    one, two = request(), request()
    page.emit('request', one); page.emit('request', two); page.emit('response', response(one))
    assert observer.ack is None and observer.ambiguous


def row(**overrides):
    return {'aweme_id': ID, 'desc': '猫咪打工日记', 'public_time': 1700000001,
        'author': {'uid': 'current-owner'}, 'visibility_type': 0,
        'status': {'is_delete': False, 'is_prohibited': False, 'in_reviewing': False, 'is_private': False}, **overrides}


def readback(module, rows, *, baseline=frozenset(), account=ACCOUNT):
    assert hasattr(module, '_verify_douyin_ack'), 'Missing same-ID authenticated readback'
    module._read_douyin_rows = lambda _page: rows
    module._read_douyin_receipt_account = lambda _page: account
    with patch.object(module.time, 'time', return_value=1700000010):
        return module._verify_douyin_ack(object(), ack_id=ID, expected_account=ACCOUNT,
            since_ms=1700000000000, baseline=baseline, attempts=1, delay_s=0)


def test_explicit_public_same_id_readback_can_satisfy_the_real_host_receipt_contract(harness):
    module = harness.load('douyin_publish')
    result = readback(module, [row()])
    from easel.publication_receipts import normalize_receipt
    host = {'operationId': 'a' * 32, 'payloadHash': 'b' * 64, 'platform': 'douyin',
        'mode': 'publish_now', 'approvedAccountId': ACCOUNT['id']}
    observation = {'kind': 'publication_receipt', 'schemaVersion': 1,
        'operationId': host['operationId'], 'payloadHash': host['payloadHash'], 'platform': 'douyin',
        'mode': 'publish_now', 'accountId': ACCOUNT['id'], 'phase': 'readback', 'account': ACCOUNT,
        'ack': {'kind': 'post', 'accepted': True, 'id': ID, 'source': 'provider_response'}, 'readback': result.as_dict()}
    normalized = normalize_receipt(host, observation)
    assert normalized['status'] == 'confirmed'
    assert normalized['reason'] == 'public_readback_confirmed'
    assert normalized['externalId'] == ID and normalized['publicationState'] == 'public'


@pytest.mark.parametrize('variant', ['missing-status', 'missing-visibility', 'creator-published-only', 'audit-zero-only',
    'reviewing', 'private', 'wrong-id', 'wrong-author', 'old-id', 'no-baseline', 'duplicate', 'wrong-account'])
def test_title_time_and_absent_or_nonpublic_fields_never_upgrade_to_confirmed(harness, variant):
    module = harness.load('douyin_publish'); item = row(); rows = [item]; baseline = frozenset(); account = ACCOUNT
    if variant == 'missing-status': item.pop('status')
    if variant == 'missing-visibility': item.pop('visibility_type')
    if variant == 'creator-published-only': item.update(status='published'); item.pop('visibility_type')
    if variant == 'audit-zero-only': item.update(audit_status=0); item.pop('visibility_type')
    if variant == 'reviewing': item['status']['in_reviewing'] = True
    if variant == 'private': item['visibility_type'] = 1
    if variant == 'wrong-id': item['aweme_id'] = '7612345678901234568'
    if variant == 'wrong-author': item['author']['uid'] = 'other-owner'
    if variant == 'old-id': baseline = frozenset({ID})
    if variant == 'no-baseline': baseline = None
    if variant == 'duplicate': rows.append(dict(item))
    if variant == 'wrong-account': account = {'id': 'uid:other-owner', 'authenticated': True}
    result = readback(module, rows, baseline=baseline, account=account)
    from easel.publication_receipts import _readback_evidence
    confirmed, _, _ = _readback_evidence(result.as_dict(), ACCOUNT['id'], ID)
    assert confirmed is False


class AiControl:
    def __init__(self, *, checked=False, reacts=True): self.checked = checked; self.reacts = reacts
    def count(self): return 1
    def is_visible(self): return True
    def is_checked(self): return self.checked
    def check(self):
        if self.reacts: self.checked = True


def test_ai_declaration_requires_the_named_native_checkbox_and_checked_readback(harness):
    module = harness.load('douyin_publish')
    assert hasattr(module, '_declare_ai_content'), 'Missing explicitly requested native AI declaration'
    control = AiControl()
    page = SimpleNamespace(get_by_role=lambda role, **kwargs: control if role == 'checkbox' and kwargs.get('name') == '内容由AI生成' else None)
    module._declare_ai_content(page)
    assert control.checked is True
    control.reacts = False; control.checked = False
    with pytest.raises(RuntimeError, match='AI'):
        module._declare_ai_content(page)


def test_ai_declaration_missing_native_control_fails_closed_instead_of_guessing_css_or_rewriting_copy(harness):
    module = harness.load('douyin_publish')
    assert hasattr(module, '_declare_ai_content')
    empty = SimpleNamespace(count=lambda: 0)
    page = SimpleNamespace(get_by_role=lambda *_a, **_kw: empty, get_by_text=lambda *_a, **_kw: empty)
    with pytest.raises(RuntimeError, match='AI'):
        module._declare_ai_content(page)


def test_real_runner_records_ack_and_strong_readback_without_a_second_submit(harness):
    harness.load('douyin_publish')
    import platform_readback
    module = douyin(harness, platform_readback.ReadbackResult('unverified'))
    args, _ = real_writer(harness, 'douyin', 'uid:observed-dy')
    args.ai_generated = True
    page = module._launch(None).pages[0]; events = Page()
    page.on, page.remove_listener = events.on, events.remove_listener
    submitted = []
    module._read_douyin_receipt_account = lambda _page: {'id': 'uid:observed-dy', 'authenticated': True}
    module._read_douyin_rows = lambda _page: [row(author={'uid': 'observed-dy'})] if submitted else []
    module._declare_ai_content = lambda _page: harness.events.append(('native-ai-checked',))
    def click(**_kwargs):
        harness.events.append(('submit-click',)); submitted.append(True)
        req = request(); req.post_data_json['item']['common']['text'] = args.title + ' ' + args.content
        events.emit('request', req); events.emit('response', response(req))
    module._wait_publish_button_ready = lambda *_args: SimpleNamespace(click=click,
        scroll_into_view_if_needed=lambda: None, bounding_box=lambda: None)
    with patch.object(module.time, 'time', return_value=1700000000):
        assert module.cmd_publish_video(args) == 0
    value = json.loads(open(args.receipt_file).read())
    assert value['ack']['id'] == ID
    assert value['readback']['evidence']['matchMethod'] == 'ack_id'
    assert value['readback']['evidence']['visibility'] == 'public'
    assert len(submitted) == 1
    assert harness.events.index(('native-ai-checked',)) < harness.events.index(('submit-click',))


def test_refresh_without_original_ack_refuses_before_opening_a_browser(harness):
    module = harness.load('douyin_publish')
    assert hasattr(module, 'cmd_refresh_receipt'), 'Missing original-receipt-only refresh command'
    args, writer = real_writer(harness, 'douyin', ACCOUNT['id'])
    reporter = writer.PublicationReceiptReporter.from_args(args, 'douyin')
    reporter.record('submit_attempted', account=ACCOUNT)
    before = open(args.receipt_file).read()
    with pytest.raises(SystemExit):
        module.cmd_refresh_receipt(args)
    assert ('browser-manager',) not in harness.events
    assert open(args.receipt_file).read() == before


def test_refresh_reads_only_same_host_receipt_id_account_and_preserves_operation_hash(harness):
    module = harness.load('douyin_publish')
    assert hasattr(module, 'cmd_refresh_receipt')
    args, writer = real_writer(harness, 'douyin', ACCOUNT['id'])
    reporter = writer.PublicationReceiptReporter.from_args(args, 'douyin')
    reporter.record('platform_ack', account=ACCOUNT,
        ack={'kind': 'post', 'accepted': True, 'source': 'provider_response', 'id': ID},
        readback={'outcome': 'unverified', 'evidence': {'baselineCaptured': True, 'sinceMs': 1700000000000}})
    before = json.loads(open(args.receipt_file).read()); queried = []
    page = SimpleNamespace(goto=lambda *_a, **_kw: None, set_default_timeout=lambda *_a: None)
    module._launch = lambda *_a, **_kw: SimpleNamespace(pages=[page], close=lambda: None)
    module._read_douyin_rows = lambda _page: [row()]
    module._read_douyin_receipt_account = lambda _page: ACCOUNT
    original = module._verify_douyin_ack
    def verify(target, **kwargs):
        queried.append(kwargs); return original(target, **kwargs)
    module._verify_douyin_ack = verify
    # Any accidental publication helper use fails this finite offline test.
    module._upload_files = module._click_publish = lambda *_a, **_kw: pytest.fail('A receipt refresh must never submit media')
    with patch.object(module.time, 'time', return_value=1700000010):
        assert module.cmd_refresh_receipt(args) == 0
    after = json.loads(open(args.receipt_file).read())
    assert queried[0]['ack_id'] == ID and queried[0]['expected_account'] == ACCOUNT
    for key in ('operationId', 'payloadHash', 'approvedAccountId', 'ack'):
        assert after[key] == before[key]
    assert after['readback']['evidence']['visibility'] == 'public'


def test_validated_receipt_snapshot_is_a_copy_and_cannot_mint_a_new_ack(harness):
    args, writer = real_writer(harness, 'douyin', ACCOUNT['id'])
    reporter = writer.PublicationReceiptReporter.from_args(args, 'douyin')
    reporter.record('platform_ack', account=ACCOUNT,
        ack={'kind': 'post', 'accepted': True, 'source': 'provider_response', 'id': ID})
    saved = reporter.snapshot(); saved['ack']['id'] = '111'; saved['account']['id'] = 'uid:another'
    assert reporter.snapshot()['ack']['id'] == ID and reporter.snapshot()['account'] == ACCOUNT


def test_explicit_ai_declaration_failure_never_clicks_publish_and_does_not_rewrite_approved_copy(harness):
    harness.load('douyin_publish')
    import platform_readback
    module = douyin(harness, platform_readback.ReadbackResult('unverified'))
    args = harness.args(); args.ai_generated = True
    original = args.content
    module._declare_ai_content = lambda _page: (_ for _ in ()).throw(RuntimeError('原生AI声明不可核实'))
    with pytest.raises(RuntimeError, match='AI'):
        module.cmd_publish_video(args)
    assert args.content == original
    assert ('submit-click',) not in harness.events


def test_discussing_ai_in_caption_does_not_infer_an_ai_generated_declaration(harness):
    harness.load('douyin_publish')
    import platform_readback
    candidate = platform_readback.ReadbackResult('verified', matched=platform_readback.WorkItem('candidate', '安全测试标题', 'published'))
    module = douyin(harness, candidate)
    args = harness.args(); args.ai_generated = False; args.content = '我对AI技术的个人看法'
    module._declare_ai_content = lambda _page: pytest.fail('AI discussion must not become an inferred declaration')
    assert module.cmd_publish_video(args) == 0


@pytest.mark.parametrize('second_shape', ['same', 'other-copy', 'malformed', 'other-media'])
def test_ack_then_second_create_persists_irreversible_ambiguity_and_cannot_be_refreshed_to_public(harness, second_shape):
    args, writer = real_writer(harness, 'douyin', ACCOUNT['id'])
    reporter = writer.PublicationReceiptReporter.from_args(args, 'douyin')
    assert hasattr(reporter, 'mark_submission_ambiguous'), 'Submission ambiguity must survive writer reopen and refresh'
    reporter.record('platform_ack', account=ACCOUNT,
        ack={'kind': 'post', 'accepted': True, 'source': 'provider_response', 'id': ID},
        readback={'outcome': 'unverified', 'evidence': {'baselineCaptured': True, 'sinceMs': 1700000000000}})
    module = harness.load('douyin_publish'); page = Page()
    observer = module._DouyinSubmitAck(page, title='猫咪打工日记', desc='先伸懒腰。', kind='video',
        on_ack=lambda *_: None, on_ambiguous=reporter.mark_submission_ambiguous)
    observer.arm(ACCOUNT); one = request(); page.emit('request', one); page.emit('response', response(one))
    assert observer.ack is not None
    second = request()
    if second_shape == 'other-copy': second.post_data_json['item']['common']['text'] = '不同内容的第二条帖子'
    if second_shape == 'malformed': second.post_data_json = None
    if second_shape == 'other-media': second.post_data_json['item']['common']['media_type'] = 2
    page.emit('request', second); assert observer.ack is None
    assert writer.PublicationReceiptReporter.from_args(args, 'douyin').snapshot()['submissionAmbiguous'] is True
    # Even later trusted-looking public evidence cannot erase the ambiguity.
    strong = readback(module, [row()])
    reporter.record('readback', account=ACCOUNT, readback=strong)
    value = json.loads(open(args.receipt_file).read()); assert value['submissionAmbiguous'] is True
    from easel.publication_receipts import normalize_receipt
    host = {'operationId': value['operationId'], 'payloadHash': value['payloadHash'], 'platform': 'douyin',
        'mode': 'publish_now', 'approvedAccountId': ACCOUNT['id']}
    result = normalize_receipt(host, value)
    assert result['status'] == 'unknown' and result['reason'] == 'ambiguous_submission'
    with pytest.raises(SystemExit):
        module.cmd_refresh_receipt(args)
    assert ('browser-manager',) not in harness.events
