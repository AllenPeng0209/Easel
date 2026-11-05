"""Native declaration selection/readback with finite DOM doubles; never a browser."""
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from tests.test_publisher_receipt_adapters import harness as _adapter_harness, real_writer, xhs, PWTimeout


TRIGGER = '.d-select-wrapper.custom-select-44[lass="declaration-wrapper"]'
DROPDOWN = '.d-popover.custom-dropdown-44.declaration-drop-down'
DESCRIPTION = '.d-select-content .d-select-description'
PLACEHOLDER = '.d-select-placeholder'
OPTION = '.d-option-content:not(.disabled) .d-option-name'
AI_LABEL = '笔记含AI合成内容'
PUBLIC_TRIGGER = '.permission-card-wrapper .d-select-wrapper.custom-select-44.permission-card-select'
ALL_SELECT_MENUS = '.d-popover.custom-dropdown-44'


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return _adapter_harness.__wrapped__(monkeypatch, tmp_path)


class Node:
    def __init__(self, text='', *, visible=True, attrs=None, click=None):
        self.text, self.visible, self.attrs = text, visible, attrs or {}
        self.children = {}
        self.on_click, self.clicks = click, 0

    def is_visible(self): return self.visible
    def get_attribute(self, name): return self.attrs.get(name)
    def inner_text(self): return self.text
    def query_selector_all(self, selector): return self.children.get(selector, [])
    def click(self):
        self.clicks += 1
        if self.on_click: self.on_click()


class DeclarationDom:
    def __init__(self, *, selected='', menu_only=False):
        self.description = Node(selected)
        self.placeholder = Node('' if selected else '添加内容类型声明')
        self.trigger = Node(attrs={'class': 'd-select-wrapper custom-select-44', 'lass': 'declaration-wrapper'})
        self.trigger.children = {DESCRIPTION: [self.description], PLACEHOLDER: [self.placeholder]}
        self.menu = Node(visible=False)
        self.option = Node(AI_LABEL)
        self.menu.children = {OPTION: [self.option]}
        self.roots, self.menus = [self.trigger], [self.menu]
        self.trigger.on_click = lambda: setattr(self.menu, 'visible', True)
        def choose():
            self.menu.visible = False  # vShow keeps the menu node in DOM.
            if not menu_only:
                self.description.text, self.placeholder.text = AI_LABEL, ''
        self.option.on_click = choose

    def query_selector_all(self, selector):
        return {TRIGGER: self.roots, DROPDOWN: self.menus}.get(selector, [])

    def wait_for_timeout(self, _milliseconds): pass


def declare(module, page):
    assert callable(getattr(module, '_declare_ai_content', None)), 'Native Select declaration helper is missing'
    return module._declare_ai_content(page)


def test_selects_the_native_option_and_reads_back_the_closed_trigger(harness):
    module, page = harness.load('xhs_publish'), DeclarationDom()
    selected = declare(module, page)
    assert selected is page.trigger
    assert page.description.text == AI_LABEL and page.placeholder.text == ''
    assert page.trigger.clicks == page.option.clicks == 1
    assert page.menu in page.menus and not page.menu.visible


def test_an_existing_correct_selection_is_idempotent_without_clicks(harness):
    module, page = harness.load('xhs_publish'), DeclarationDom(selected=AI_LABEL)
    assert declare(module, page) is page.trigger
    assert page.trigger.clicks == page.option.clicks == 0


@pytest.mark.parametrize('failure', ['missing', 'ambiguous', 'hidden', 'disabled', 'readonly',
    'other-declaration', 'wrong-placeholder', 'description-ambiguous', 'placeholder-missing', 'open-menu'])
def test_uncertain_or_conflicting_declaration_controls_never_get_clicked(harness, failure):
    module, page = harness.load('xhs_publish'), DeclarationDom()
    if failure == 'missing': page.roots = []
    if failure == 'ambiguous': page.roots.append(Node())
    if failure == 'hidden': page.trigger.visible = False
    if failure in ('disabled', 'readonly'):
        page.trigger.children['.d-select.disabled, .d-select.readonly'] = [Node()]
    if failure == 'other-declaration': page.description.text = '个人观点，仅供参考'
    if failure == 'wrong-placeholder': page.placeholder.text = '另一个内容选择器'
    if failure == 'description-ambiguous': page.trigger.children[DESCRIPTION].append(Node())
    if failure == 'placeholder-missing': page.trigger.children[PLACEHOLDER] = []
    if failure == 'open-menu': page.menu.visible = True
    assert callable(getattr(module, '_declare_ai_content', None)), 'Native Select declaration helper is missing'
    with pytest.raises(RuntimeError): module._declare_ai_content(page)
    assert page.trigger.clicks == page.option.clicks == 0


@pytest.mark.parametrize('failure', ['no-option', 'disabled-option', 'wrong-label', 'duplicate-option',
    'duplicate-menu', 'option-only', 'menu-stays-open', 'trigger-detached'])
def test_a_dropdown_option_alone_cannot_prove_selected_declaration(harness, failure):
    module, page = harness.load('xhs_publish'), DeclarationDom(menu_only=failure == 'option-only')
    if failure in ('no-option', 'disabled-option'): page.menu.children[OPTION] = []
    if failure == 'wrong-label': page.option.text = '关于笔记含AI合成内容的帮助'
    if failure == 'duplicate-option': page.menu.children[OPTION].append(Node(AI_LABEL))
    if failure == 'duplicate-menu':
        extra = Node()
        page.trigger.on_click = lambda: [setattr(m, 'visible', True) for m in page.menus]
        page.menus.append(extra)
        extra.visible = False
    if failure == 'menu-stays-open':
        def choose(): page.description.text, page.placeholder.text = AI_LABEL, ''
        page.option.on_click = choose
    if failure == 'trigger-detached':
        choose = page.option.on_click
        def detach(): choose(); page.trigger.visible = False
        page.option.on_click = detach
    assert callable(getattr(module, '_declare_ai_content', None)), 'Native Select declaration helper is missing'
    with pytest.raises(RuntimeError): module._declare_ai_content(page)
    assert page.trigger.clicks == 1 and page.option.clicks <= 1


def test_hidden_portals_and_hidden_duplicate_controls_do_not_replace_the_visible_trigger(harness):
    module, page = harness.load('xhs_publish'), DeclarationDom()
    page.roots.append(Node(visible=False))
    page.menus.append(Node(visible=False))
    page.menu.children[OPTION].append(Node(AI_LABEL, visible=False))
    assert declare(module, page) is page.trigger
    assert page.option.clicks == 1


def test_final_readback_is_readonly_and_rejects_a_lost_selection(harness):
    module, page = harness.load('xhs_publish'), DeclarationDom(selected=AI_LABEL)
    assert callable(getattr(module, '_verify_ai_content', None)), 'Final native declaration verification is missing'
    module._verify_ai_content(page, page.trigger)
    page.description.text = ''
    with pytest.raises(RuntimeError): module._verify_ai_content(page, page.trigger)
    assert page.trigger.clicks == page.option.clicks == 0


def test_a_hidden_selected_description_cannot_satisfy_final_readback(harness):
    module, page = harness.load('xhs_publish'), DeclarationDom(selected=AI_LABEL)
    page.description.visible = False
    with pytest.raises(RuntimeError): module._verify_ai_content(page, page.trigger)
    assert page.trigger.clicks == page.option.clicks == 0


def prepared_runner(harness, *, selected=''):
    module = xhs(harness, account={'userId': 'approved-xhs'})
    args, writer = real_writer(harness, 'xiaohongshu', 'userId:approved-xhs')
    args.ai_generated = True
    page = module._launch(None).pages[0]
    dom = DeclarationDom(selected=selected)
    page.query_selector_all = dom.query_selector_all
    return module, args, writer, page, dom


def test_ai_generated_publishing_reaches_one_submit_only_after_native_selection(harness):
    module, args, _, _, dom = prepared_runner(harness)
    assert module.cmd_publish(args) == 0
    assert ('upload-media',) in harness.events
    assert dom.description.text == AI_LABEL
    assert harness.events.count(('submit-click',)) == 1
    receipt = json.loads(Path(args.receipt_file).read_text())
    assert receipt['phase'] == 'submit_attempted'  # UI success still is not public evidence.


@pytest.mark.parametrize('failure', ['missing-control', 'selection-lost-after-copy'])
def test_ai_declaration_failure_after_upload_keeps_unknown_and_never_clicks_publish(harness, failure):
    module, args, _, _, dom = prepared_runner(harness)
    if failure == 'missing-control': dom.roots = []
    if failure == 'selection-lost-after-copy':
        harness.monkeypatch.setattr(module, '_input_tags', lambda *_: setattr(dom.description, 'text', ''))
    with pytest.raises(RuntimeError): module.cmd_publish(args)
    assert ('upload-media',) in harness.events and ('submit-click',) not in harness.events
    receipt = json.loads(Path(args.receipt_file).read_text())
    assert receipt['phase'] == 'submit_attempted' and receipt['reason'] == 'submit_error'
    from easel.publication_receipts import normalize_receipt
    normalized = normalize_receipt({'operationId': receipt['operationId'], 'payloadHash': receipt['payloadHash'],
        'platform': 'xiaohongshu', 'mode': 'publish_now', 'approvedAccountId': 'userId:approved-xhs'}, receipt)
    assert normalized['status'] == 'unknown' and not normalized.get('notStarted')


@pytest.mark.parametrize('failure', ['ai-changed', 'account-changed'])
def test_second_confirmation_rechecks_ai_and_account_without_reselecting_or_clicking_again(harness, failure):
    module, args, writer, _, dom = prepared_runner(harness)
    def confirm(_page, before_confirm=None):
        if failure == 'ai-changed': dom.description.text = '个人观点，仅供参考'
        else: harness.monkeypatch.setattr(module, '_creator_account', lambda _: {'userId': 'other-user'})
        assert before_confirm is not None
        before_confirm()
        harness.events.append(('confirmation-click',))
    harness.monkeypatch.setattr(module, '_confirm_publish_dialog', confirm)
    with pytest.raises((RuntimeError, writer.PublicationReceiptError)): module.cmd_publish(args)
    assert harness.events.count(('submit-click',)) == 1
    assert ('confirmation-click',) not in harness.events and dom.option.clicks == 1
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'submit_attempted'


def test_cli_accepts_host_public_requirement_without_starting_a_browser_on_dry_run(harness, monkeypatch):
    module = harness.load('xhs_publish'); args = harness.args()
    monkeypatch.setattr(__import__('sys'), 'argv', ['xhs_publish.py', 'publish', '--title', args.title,
        '--images', args.images, '--require-public'])
    assert module.main() == 0
    assert harness.factories[-1][0].require_public is True
    assert ('browser-manager',) not in harness.events


class PublicDom:
    def __init__(self, label='公开可见'):
        self.trigger = Node(attrs={'class': 'd-select-wrapper custom-select-44 permission-card-select'})
        self.description = Node(label)
        self.trigger.children = {DESCRIPTION: [self.description]}
        self.roots, self.menus = [self.trigger], []

    def query_selector_all(self, selector):
        return {PUBLIC_TRIGGER: self.roots, ALL_SELECT_MENUS: self.menus}.get(selector, [])


@pytest.mark.parametrize('placeholder', [None, ''])
def test_host_public_gate_reads_the_form_selection_without_opening_or_changing_it(harness, placeholder):
    module, page = harness.load('xhs_publish'), PublicDom()
    if placeholder is not None: page.trigger.children[PLACEHOLDER] = [Node(placeholder)]
    assert callable(getattr(module, '_require_public_content', None)), 'Native public audience gate is missing'
    module._require_public_content(page)
    assert page.trigger.clicks == 0


@pytest.mark.parametrize('failure', ['friends', 'private', 'partial', 'missing', 'duplicate', 'hidden-description',
    'duplicate-description', 'wrong-label', 'placeholder-content', 'open-control', 'disabled', 'readonly', 'visible-menu'])
def test_host_public_gate_refuses_nonpublic_or_unreadable_form_state_without_changing_it(harness, failure):
    module, page = harness.load('xhs_publish'), PublicDom()
    if failure == 'friends': page.description.text = '仅互关好友可见'
    if failure == 'private': page.description.text = '仅自己可见'
    if failure == 'partial': page.description.text = '部分人可见'
    if failure == 'missing': page.roots = []
    if failure == 'duplicate': page.roots.append(Node())
    if failure == 'hidden-description': page.description.visible = False
    if failure == 'duplicate-description': page.trigger.children[DESCRIPTION].append(Node('公开可见'))
    if failure == 'wrong-label': page.description.text = '公开可见帮助'
    if failure == 'placeholder-content': page.trigger.children[PLACEHOLDER] = [Node('请选择')]
    if failure in ('open-control', 'disabled', 'readonly'):
        page.trigger.children['.d-select.disabled, .d-select.readonly, .d-select.focus'] = [Node()]
    if failure == 'visible-menu': page.menus.append(Node())
    assert callable(getattr(module, '_require_public_content', None)), 'Native public audience gate is missing'
    with pytest.raises(RuntimeError): module._require_public_content(page)
    assert page.trigger.clicks == 0


def strong_runner(harness, *, baseline_failure=False):
    module, args, writer, page, ai = prepared_runner(harness)
    args.require_public = True
    public = PublicDom()
    def query(selector):
        if selector == ALL_SELECT_MENUS: return ai.menus + public.menus
        return ai.query_selector_all(selector) + public.query_selector_all(selector)
    page.query_selector_all = query
    context = SimpleNamespace(pages=[page], storage_state=lambda: {'cookies': [], 'origins': []},
        close=lambda: harness.events.append(('close-context',)))
    harness.monkeypatch.setattr(module, '_launch', lambda *_a, **_k: context)
    api = ModuleType('easel.xhs_publication_readback')
    reader = SimpleNamespace(close=lambda: harness.events.append(('close-reader',)))
    api.ReadOnlyClient = SimpleNamespace(from_storage_state=lambda state, **kw: reader)
    baseline = object()
    def capture(client, host):
        assert client is reader and set(host) == {'operationId', 'payloadHash', 'platform', 'mode', 'approvedAccountId'}
        harness.events.append(('capture-baseline',))
        if baseline_failure: raise RuntimeError('baseline unavailable')
        return baseline
    def proof(value, host, *, ack=None, **_kwargs):
        assert value is baseline and host['approvedAccountId'] == 'userId:approved-xhs'
        return {'outcome': 'unverified', 'evidence': {'baselineCaptured': ack is not None,
            'sinceMs': 1000, 'checkedAtMs': 1100, 'account': {'id': 'userId:approved-xhs', 'authenticated': True}}}
    api.capture_baseline = capture
    api.baseline_evidence = proof
    api.ack_baseline_evidence = lambda value, ack, host, **kw: proof(value, host, ack=ack)
    def verify(client, host, receipt, **_kwargs):
        assert client is reader and receipt['ack']['id'] == '67a012345678901234567890'
        assert receipt['readback']['evidence']['baselineCaptured'] is True
        harness.events.append(('verify-ack',))
        return proof(baseline, host, ack=receipt['ack'])  # No synthetic public success.
    api.verify_ack = verify
    harness.monkeypatch.setitem(__import__('sys').modules, 'easel.xhs_publication_readback', api)
    harness.monkeypatch.setattr(__import__('easel'), 'xhs_publication_readback', api, raising=False)
    return module, args, writer, page, ai, public


def test_host_requires_successful_fresh_baseline_before_any_final_click(harness):
    module, args, _, _, _, _ = strong_runner(harness, baseline_failure=True)
    with pytest.raises(RuntimeError): module.cmd_publish(args)
    assert ('upload-media',) in harness.events and ('capture-baseline',) in harness.events
    assert ('submit-click',) not in harness.events and ('close-reader',) in harness.events
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'submit_attempted'


def test_host_fresh_baseline_is_persisted_without_claiming_publication_when_no_ack_arrives(harness):
    module, args, _, _, _, _ = strong_runner(harness)
    assert module.cmd_publish(args) == 0
    assert harness.events.index(('capture-baseline',)) < harness.events.index(('submit-click',))
    receipt = json.loads(Path(args.receipt_file).read_text())
    assert receipt['readback']['evidence']['sinceMs'] == 1000
    assert receipt['readback']['evidence']['baselineCaptured'] is False
    assert 'ack' not in receipt and ('verify-ack',) not in harness.events
    assert harness.events.count(('submit-click',)) == 1


@pytest.mark.parametrize('moment', ['before-primary', 'before-confirm'])
def test_host_blocks_actual_nonpublic_audience_at_each_final_click(harness, moment):
    module, args, _, _, _, public = strong_runner(harness)
    if moment == 'before-primary':
        harness.monkeypatch.setattr(module, '_input_tags', lambda *_: setattr(public.description, 'text', '仅互关好友可见'))
    else:
        def confirm(_page, before_confirm=None):
            public.description.text = '仅互关好友可见'
            before_confirm()
            harness.events.append(('confirmation-click',))
        harness.monkeypatch.setattr(module, '_confirm_publish_dialog', confirm)
    with pytest.raises(RuntimeError): module.cmd_publish(args)
    assert harness.events.count(('submit-click',)) == (0 if moment == 'before-primary' else 1)
    assert ('confirmation-click',) not in harness.events and public.trigger.clicks == 0


@pytest.mark.parametrize('outcome', ['ack', 'ui-timeout', 'ambiguous-second-post'])
def test_host_binds_baseline_atomically_to_exact_ack_and_never_verifies_an_ambiguous_submit(harness, outcome):
    from tests.test_xhs_publication_ack import PageEvents, request, response
    module, args, writer, page, _, _ = strong_runner(harness)
    args.tags = ''
    events = PageEvents()
    page.on, page.remove_listener = events.on, events.remove_listener
    observed_ack_writes = []
    original_record = writer.PublicationReceiptReporter.record
    def record(self, phase, **kwargs):
        result = original_record(self, phase, **kwargs)
        if kwargs.get('ack') is not None:
            snapshot = json.loads(Path(args.receipt_file).read_text())
            observed_ack_writes.append(snapshot.get('readback', {}).get('evidence', {}).get('baselineCaptured'))
        return result
    harness.monkeypatch.setattr(writer.PublicationReceiptReporter, 'record', record)
    def click(**_kwargs):
        harness.events.append(('submit-click',))
        req = request()
        req.post_data_json['common'].update(title=args.title, desc=args.content, type='normal')
        events.emit('request', req); events.emit('response', response(req))
        if outcome == 'ambiguous-second-post': events.emit('request', request(post_data_json=None))
    harness.monkeypatch.setattr(module, '_wait_publish_clickable', lambda *_: ('old', SimpleNamespace(
        click=click, scroll_into_view_if_needed=lambda: None, bounding_box=lambda: None)))
    if outcome == 'ui-timeout':
        def timeout(*_): raise PWTimeout('UI timeout after exact ACK')
        harness.monkeypatch.setattr(module, '_wait_publish_success', timeout)
        with pytest.raises(SystemExit): module.cmd_publish(args)
    else:
        assert module.cmd_publish(args) == 0
    assert observed_ack_writes and all(value is True for value in observed_ack_writes)
    receipt = json.loads(Path(args.receipt_file).read_text())
    assert receipt['ack']['id'] == '67a012345678901234567890'
    assert harness.events.count(('capture-baseline',)) == harness.events.count(('submit-click',)) == 1
    if outcome == 'ambiguous-second-post':
        assert receipt['submissionAmbiguous'] is True and ('verify-ack',) not in harness.events
    else:
        assert receipt['phase'] == 'readback' and harness.events.count(('verify-ack',)) == 1
    assert ('close-reader',) in harness.events


def test_invalid_readback_evidence_still_closes_the_owned_context_and_observer(harness):
    from tests.test_xhs_publication_ack import PageEvents, request, response
    module, args, writer, page, _, _ = strong_runner(harness); args.tags = ''
    events = PageEvents(); page.on, page.remove_listener = events.on, events.remove_listener
    __import__('sys').modules['easel.xhs_publication_readback'].verify_ack = lambda *_: {'outcome': 'not-valid'}
    def click(**_kwargs):
        req = request(); req.post_data_json['common'].update(title=args.title, desc=args.content, type='normal')
        events.emit('request', req); events.emit('response', response(req))
    harness.monkeypatch.setattr(module, '_wait_publish_clickable', lambda *_: ('old', SimpleNamespace(
        click=click, scroll_into_view_if_needed=lambda: None, bounding_box=lambda: None)))
    with pytest.raises(writer.PublicationReceiptError): module.cmd_publish(args)
    assert ('close-context',) in harness.events and ('close-reader',) in harness.events
    assert events.handlers == {}
