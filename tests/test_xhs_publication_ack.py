"""Exact XHS submission evidence; no real browser, socket or process may run."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_publisher_receipt_adapters import harness as _adapter_harness, real_writer, xhs, PWTimeout

URL = 'https://edith.xiaohongshu.com/web_api/sns/v2/note'
ACCOUNT = {'id': 'userId:approved-xhs', 'authenticated': True}
NOTE = '67a012345678901234567890'


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return _adapter_harness.__wrapped__(monkeypatch, tmp_path)


class PageEvents:
    def __init__(self): self.handlers = {}
    def on(self, event, callback): self.handlers[event] = callback
    def remove_listener(self, event, callback):
        if self.handlers.get(event) is callback: self.handlers.pop(event)
    def emit(self, event, value):
        callback = self.handlers.get(event)
        if callback: callback(value)


def request(**changes):
    return SimpleNamespace(**{'url': URL, 'method': 'POST', 'post_data_json': {'common': {
        'title': '先听孩子说完', 'desc': '先听完，再一起想办法。', 'type': 'video', 'note_id': '',
        'privacy_info': {'op_type': 1, 'type': 0, 'user_ids': []}}}, **changes})


def response(req, payload=None, *, status=200):
    return SimpleNamespace(request=req, url=req.url, status=status,
        json=lambda: {'success': True, 'code': 0, 'data': {'id': NOTE}} if payload is None else payload)


def observer(module, page, *, record=None, ambiguous=None):
    assert hasattr(module, '_XhsSubmitAck'), 'Missing exact XHS provider ACK observation'
    return module._XhsSubmitAck(page, title='先听孩子说完', content='先听完，再一起想办法。', tags=[], kind='video',
        on_ack=record or (lambda *_: None), on_ambiguous=ambiguous)


def test_only_the_exact_armed_request_produces_a_thin_provider_ack(harness):
    module = harness.load('xhs_publish'); page = PageEvents(); seen = []
    capture = observer(module, page, record=lambda account, ack: seen.append((account, ack)))
    earlier = request(); page.emit('request', earlier); page.emit('response', response(earlier))
    assert capture.ack is None
    capture.arm(ACCOUNT)
    own = request(); page.emit('request', own)
    page.emit('response', response(request()))
    assert capture.ack is None
    page.emit('response', response(own))
    assert capture.ack == {'kind': 'post', 'accepted': True, 'source': 'provider_response', 'id': NOTE, 'state': 'submitted'}
    assert seen == [(ACCOUNT, capture.ack)]
    capture.close(); assert page.handlers == {}


@pytest.mark.parametrize('change', ['http', 'foreign-host', 'creator-host', 'publish-substring', 'get', 'wrong-title', 'wrong-body', 'wrong-type'])
def test_other_endpoints_or_content_cannot_be_bound_to_this_approved_submission(harness, change):
    module = harness.load('xhs_publish'); page = PageEvents(); capture = observer(module, page); capture.arm(ACCOUNT)
    req = request()
    if change == 'http': req.url = URL.replace('https:', 'http:')
    if change == 'foreign-host': req.url = URL.replace('edith.xiaohongshu.com', 'edith.xiaohongshu.com.attacker.invalid')
    if change == 'creator-host': req.url = URL.replace('edith.', 'creator.')
    if change == 'publish-substring': req.url = 'https://edith.xiaohongshu.com/other/publish'
    if change == 'get': req.method = 'GET'
    if change == 'wrong-title': req.post_data_json['common']['title'] = '其他标题'
    if change == 'wrong-body': req.post_data_json['common']['desc'] = '其他正文'
    if change == 'wrong-type': req.post_data_json['common']['type'] = 'normal'
    page.emit('request', req); page.emit('response', response(req))
    assert capture.ack is None


@pytest.mark.parametrize('payload', [
    {'success': False, 'code': 0, 'data': {'id': NOTE}},
    {'success': True, 'code': 500, 'data': {'id': NOTE}},
    {'code': False, 'data': {'id': NOTE}},
    {'code': 'success', 'data': {'id': NOTE}},
    {'data': {'id': NOTE}}, {'success': True, 'data': {}},
    {'code': 0, 'data': {'id': '0'}}, {'code': 0, 'data': {'id': 123}},
    {'code': 0, 'data': {'id': NOTE, 'note_id': 'different-note'}},
])
def test_missing_or_contradictory_business_ack_is_never_accepted(harness, payload):
    module = harness.load('xhs_publish'); page = PageEvents(); capture = observer(module, page); capture.arm(ACCOUNT)
    req = request(); page.emit('request', req); page.emit('response', response(req, payload))
    assert capture.ack is None


@pytest.mark.parametrize('sequence', ['before-ack', 'after-ack', 'after-ack-other-body', 'invalid-first'])
def test_a_second_exact_create_post_is_irreversibly_ambiguous_even_with_an_existing_ack(harness, sequence):
    module = harness.load('xhs_publish'); page = PageEvents(); marked = []
    capture = observer(module, page, ambiguous=lambda: marked.append(True)); capture.arm(ACCOUNT)
    one, two = request(), request()
    if sequence == 'invalid-first': one.post_data_json = None
    page.emit('request', one)
    if sequence.startswith('after-ack'): page.emit('response', response(one))
    if sequence == 'after-ack-other-body': two.post_data_json = None
    page.emit('request', two); page.emit('response', response(two))
    assert capture.ack is None and capture.ambiguous and marked == [True]


def test_ai_generated_video_with_missing_native_control_stops_after_staging_without_publish_click(harness):
    module = xhs(harness, account={'userId': 'approved-xhs'})
    args, _ = real_writer(harness, 'xiaohongshu', ACCOUNT['id']); args.ai_generated = True
    module._launch(None).pages[0].query_selector_all = lambda _selector: []
    module._upload_video = lambda *_: harness.events.append(('upload-media',))
    with pytest.raises(RuntimeError): module.cmd_publish_video(args)
    assert ('browser-manager',) in harness.events and ('upload-media',) in harness.events
    assert ('submit-click',) not in harness.events
    value = json.loads(Path(args.receipt_file).read_text())
    assert value['phase'] == 'submit_attempted' and value['reason'] == 'submit_error'
    from easel.publication_receipts import normalize_receipt
    host = {'operationId': value['operationId'], 'payloadHash': value['payloadHash'], 'platform': 'xiaohongshu',
        'mode': 'publish_now', 'approvedAccountId': ACCOUNT['id']}
    normalized = normalize_receipt(host, value)
    assert normalized['status'] == 'unknown' and not normalized.get('notStarted')


@pytest.mark.parametrize('kind', ['image', 'video'])
@pytest.mark.parametrize('outcome', ['ack', 'ui-timeout', 'second-post'])
def test_real_publisher_and_private_writer_retain_only_submitted_not_public_when_ack_is_present(harness, kind, outcome):
    module = xhs(harness, account={'userId': 'approved-xhs'})
    args, _ = real_writer(harness, 'xiaohongshu', ACCOUNT['id']); args.tags = ''; args.ai_generated = False
    page = module._launch(None).pages[0]; events = PageEvents()
    page.on, page.remove_listener = events.on, events.remove_listener
    def click(**_kwargs):
        harness.events.append(('submit-click',))
        req = request(); common = req.post_data_json['common']
        common.update(title=args.title, desc=args.content, type='normal' if kind == 'image' else 'video')
        events.emit('request', req)
        # Uncalibrated fields must not upgrade the thin receipt.
        events.emit('response', response(req, {'success': True, 'code': 0,
            'data': {'note_id': NOTE, 'status': 'public', 'audit_status': 0, 'privacy_info': {'type': 0}}}))
        if outcome == 'second-post':
            events.emit('request', request(post_data_json=None))
    module._wait_publish_clickable = lambda *_: ('old', SimpleNamespace(
        click=click, scroll_into_view_if_needed=lambda: None, bounding_box=lambda: None))
    command = module.cmd_publish if kind == 'image' else module.cmd_publish_video
    if outcome == 'ui-timeout':
        def timeout(*_args): raise PWTimeout('UI signal unavailable after ACK')
        module._wait_publish_success = timeout
        with pytest.raises(SystemExit): command(args)
    else:
        assert command(args) == 0
    value = json.loads(Path(args.receipt_file).read_text())
    assert value['phase'] == 'platform_ack' and value['ack']['id'] == NOTE
    from easel.publication_receipts import normalize_receipt
    host = {'operationId': value['operationId'], 'payloadHash': value['payloadHash'], 'platform': 'xiaohongshu',
        'mode': 'publish_now', 'approvedAccountId': ACCOUNT['id']}
    normalized = normalize_receipt(host, value)
    if outcome == 'second-post':
        assert normalized['status'] == 'unknown' and normalized['reason'] == 'ambiguous_submission'
        assert value['submissionAmbiguous'] is True
    else:
        assert normalized['status'] == 'submitted' and normalized['publicationState'] == 'unknown'
        assert normalized['externalId'] == NOTE and normalized['evidenceMethod'] == 'provider_response'
    assert harness.events.count(('submit-click',)) == 1


def test_cli_parses_explicit_ai_generated_flag_on_a_dry_run(harness, monkeypatch, capsys):
    module = harness.load('xhs_publish'); args = harness.args()
    monkeypatch.setattr(__import__('sys'), 'argv', ['xhs_publish.py', 'publish', '--title', args.title,
        '--images', args.images, '--ai-generated'])
    assert module.main() == 0  # Dry-run does not open the platform.
    assert harness.factories[-1][0].ai_generated is True
    assert ('browser-manager',) not in harness.events
    assert '未验证现场控件' in capsys.readouterr().out


def test_a_caption_discussing_ai_does_not_infer_an_ai_generated_flag(harness):
    module = xhs(harness, account={'userId': 'approved-xhs'})
    args = harness.args(); args.ai_generated = False
    args.content = '这里讨论「由 AI 生成」的声明含义。'
    assert module.cmd_publish(args) == 0
    assert ('browser-manager',) in harness.events and harness.events.count(('submit-click',)) == 1


@pytest.mark.parametrize('description,accepted', [
    ('先听完，再一起想办法。 #教育[话题]# ', True),
    ('先听完，再一起想办法。 #教育', True),
    ('先听完，再一起想办法。 其他未批准内容 #教育', False),
])
def test_topic_markup_does_not_authorize_other_caption_text(harness, description, accepted):
    module = harness.load('xhs_publish'); page = PageEvents()
    capture = module._XhsSubmitAck(page, title='先听孩子说完', content='先听完，再一起想办法。', tags=['教育'],
        kind='video', on_ack=lambda *_: None)
    capture.arm(ACCOUNT); req = request(); req.post_data_json['common']['desc'] = description
    page.emit('request', req); page.emit('response', response(req))
    assert (capture.ack is not None) is accepted
