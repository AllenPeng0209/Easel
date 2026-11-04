"""B30 bound XHS validates the creator account and never retries uncertain clicks."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_publisher_receipt_adapters import PWTimeout, harness, real_writer, xhs

APPROVED = {'userId': 'approved-xhs'}
OTHER = {'userId': 'other-xhs'}


def bound(harness, *, click_failure=None):
    module = xhs(harness, account=APPROVED, click_failure=click_failure)
    args, writer = real_writer(harness, 'xiaohongshu', 'userId:approved-xhs')
    return module, args, writer


@pytest.mark.parametrize('creator', [None, OTHER])
def test_bound_requires_exact_creator_identity_before_upload(harness, creator):
    module, args, writer = bound(harness)
    harness.monkeypatch.setattr(module, '_creator_account', lambda _: creator)
    with pytest.raises(writer.PublicationReceiptError):
        module.cmd_publish(args)
    assert ('upload-media',) not in harness.events and ('submit-click',) not in harness.events
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'not_started'


def test_account_change_during_staging_blocks_primary_click(harness):
    module, args, writer = bound(harness)
    identity = iter([APPROVED, APPROVED, APPROVED, OTHER])
    harness.monkeypatch.setattr(module, '_creator_account', lambda _: next(identity))
    with pytest.raises(writer.PublicationReceiptError):
        module.cmd_publish(args)
    assert ('upload-media',) in harness.events and ('submit-click',) not in harness.events
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'submit_attempted'


def test_account_change_during_tab_wait_never_uploads(harness):
    module, args, writer = bound(harness)
    identity = iter([APPROVED, OTHER])
    harness.monkeypatch.setattr(module, '_creator_account', lambda _: next(identity))
    with pytest.raises(writer.PublicationReceiptError):
        module.cmd_publish(args)
    assert ('upload-media',) not in harness.events
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'not_started'


def test_ambiguous_bound_click_is_never_retried(harness):
    module, args, _ = bound(harness, click_failure=PWTimeout('dispatched then disconnected'))
    harness.monkeypatch.setattr(module, '_creator_account', lambda _: APPROVED)
    with pytest.raises(SystemExit):
        module.cmd_publish(args)
    assert harness.events.count(('submit-click',)) == 1
    receipt = json.loads(Path(args.receipt_file).read_text())
    assert receipt['phase'] == 'submit_attempted' and receipt['reason'] == 'submit_error'


def test_unbound_cli_keeps_original_force_click_fallback(harness):
    module = xhs(harness, click_failure=PWTimeout('legacy force click'))
    args = harness.args()
    args.receipt_context = args.receipt_file = None
    with pytest.raises(SystemExit):
        module.cmd_publish(args)
    assert harness.events.count(('submit-click',)) == 2


def test_bound_confirmation_rechecks_account_and_never_clicks_wrong_identity(harness):
    module, args, writer = bound(harness)
    identity = iter([APPROVED, APPROVED, APPROVED, APPROVED, OTHER])
    harness.monkeypatch.setattr(module, '_creator_account', lambda _: next(identity))
    dialog_code = harness.load('xhs_publish')._confirm_publish_dialog

    class Button:
        def is_visible(self): return True
        def inner_text(self): return '确认发布'
        def click(self): harness.events.append(('modal-click',))

    button = Button()
    modal = SimpleNamespace(is_visible=lambda: True, inner_text=lambda: '确认发布这篇笔记？',
                            query_selector_all=lambda _: [button])
    dialog_page = SimpleNamespace(url=module.PUBLISH_URL,
                                  query_selector_all=lambda selector: [button] if selector.startswith('button') else [modal],
                                  wait_for_timeout=lambda _: None)
    harness.monkeypatch.setattr(module, '_confirm_publish_dialog', lambda _page, **kwargs: dialog_code(dialog_page, **kwargs))
    with pytest.raises(writer.PublicationReceiptError):
        module.cmd_publish(args)
    assert harness.events.count(('submit-click',)) == 1 and ('modal-click',) not in harness.events
    assert json.loads(Path(args.receipt_file).read_text())['phase'] == 'submit_attempted'


@pytest.mark.parametrize('lose_ack', [False, True])
def test_bound_confirmation_clicks_one_explicit_modal_action_after_guard(harness, lose_ack):
    module = harness.load('xhs_publish')
    observed = []

    class Button:
        def __init__(self, label): self.label = label
        def is_visible(self): return True
        def inner_text(self): return self.label
        def click(self):
            observed.append(self.label)
            if lose_ack: raise PWTimeout('modal click dispatched but not acknowledged')

    buttons = [Button('确认'), Button('确认发布'), Button('继续发布')]
    modal = SimpleNamespace(is_visible=lambda: True, inner_text=lambda: '确认发布这篇笔记？',
                            query_selector_all=lambda _: buttons)
    page = SimpleNamespace(url=module.PUBLISH_URL, query_selector_all=lambda _: [modal], wait_for_timeout=lambda _: None)
    if lose_ack:
        with pytest.raises(PWTimeout):
            module._confirm_publish_dialog(page, before_confirm=lambda: observed.append('checked'))
    else:
        module._confirm_publish_dialog(page, before_confirm=lambda: observed.append('checked'))
    assert observed == ['checked', '确认发布']


def test_bound_confirmation_does_not_scan_buttons_after_navigation(harness):
    module = harness.load('xhs_publish')
    def unexpected(*_):
        pytest.fail('A different page cannot contain this submission confirmation')
    page = SimpleNamespace(url='https://creator.xiaohongshu.com/new/manage', query_selector_all=unexpected)
    module._confirm_publish_dialog(page, before_confirm=unexpected)
