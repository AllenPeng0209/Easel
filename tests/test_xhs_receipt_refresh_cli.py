"""The refresh CLI can only read one original, ACK-bound Host operation."""
import json
import sys
import time
from types import SimpleNamespace

import pytest

from tests.test_publisher_receipt_adapters import harness as _adapter_harness
from tests.test_xhs_publication_readback import (
    ACCOUNT, CONTEXT, NOTE, Reader, ack, creator, main, private_reporter,
)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    return _adapter_harness.__wrapped__(monkeypatch, tmp_path)


def original(harness, *, state='ack'):
    import easel.xhs_publication_readback as api
    writer, files, reporter = private_reporter(harness.tmp_path)
    since = int(time.time() * 1000) - 1000
    baseline = api.capture_baseline(Reader(), CONTEXT, now_ms=lambda: since)
    reporter.record('submit_attempted', account=ACCOUNT)
    if state != 'legacy-ack':
        reporter.record('submit_attempted', account=ACCOUNT,
            readback=api.baseline_evidence(baseline, CONTEXT, now_ms=lambda: since + 10))
    if state != 'no-ack':
        kwargs = {} if state == 'legacy-ack' else {
            'readback': api.ack_baseline_evidence(baseline, ack(), CONTEXT, now_ms=lambda: since + 20)}
        reporter.record('platform_ack', account=ACCOUNT, ack=ack(), **kwargs)
    if state == 'ambiguous': reporter.mark_submission_ambiguous()
    harness.monkeypatch.setitem(sys.modules, 'publication_receipt', writer)
    args = SimpleNamespace(receipt_context=str(files.context_path), receipt_file=str(files.receipt_path), no_proxy=True)
    return api, writer, files, args, since


def command(module):
    assert callable(getattr(module, 'cmd_refresh_receipt', None)), 'Missing GET-only XHS receipt refresh command'
    return module.cmd_refresh_receipt


def test_refresh_reads_the_exact_original_ack_with_real_helper_and_writer_without_a_browser(harness, monkeypatch):
    api, _, files, args, since = original(harness)
    before = files.read(); events = []
    reader = Reader(creators=[[creator()]], mains=[[main(time=since + 400)]])
    reader.close = lambda: events.append('closed')
    workspace = harness.tmp_path / 'current-ip'
    def factory(path, *, proxy=''):
        assert path == workspace and proxy == ''
        events.append('reader')
        return reader
    monkeypatch.setenv('EASEL_ROOT', str(workspace))
    monkeypatch.setattr(api.ReadOnlyClient, 'from_current_session', factory)
    monkeypatch.setattr(api, 'capture_baseline', lambda *_a, **_k: pytest.fail('A refresh must not create a new baseline'))
    module = harness.load('xhs_publish')
    assert command(module)(args) == 0
    after = files.read()
    assert all(after[key] == before[key] for key in (*CONTEXT, 'ack', 'account'))
    assert after['readback']['evidence']['sinceMs'] == since
    assert after['readback']['evidence']['visibility'] == 'public'
    assert reader.calls == ['identity', 'creator', 'main', 'identity']
    assert events == ['reader', 'closed'] and ('browser-manager',) not in harness.events
    from easel.publication_receipts import normalize_receipt
    assert normalize_receipt(CONTEXT, after)['status'] == 'confirmed'


@pytest.mark.parametrize('state', ['no-ack', 'legacy-ack', 'ambiguous'])
def test_refresh_refuses_incomplete_or_ambiguous_original_evidence_before_loading_credentials(harness, monkeypatch, state):
    api, _, files, args, _ = original(harness, state=state)
    before = files.read()
    monkeypatch.setattr(api.ReadOnlyClient, 'from_current_session',
        lambda *_a, **_k: pytest.fail('Incomplete original evidence must not load credentials'))
    monkeypatch.setattr(api, 'capture_baseline', lambda *_a, **_k: pytest.fail('No new baseline'))
    module = harness.load('xhs_publish')
    with pytest.raises(SystemExit) as stopped: command(module)(args)
    assert stopped.value.code != 0 and files.read() == before
    assert ('browser-manager',) not in harness.events


def test_refresh_without_a_private_host_context_cannot_load_a_reader(harness):
    original(harness)  # Use the real writer's disabled/null reporter.
    module = harness.load('xhs_publish')
    with pytest.raises(SystemExit):
        command(module)(SimpleNamespace(receipt_context=None, receipt_file=None, no_proxy=True))
    assert ('browser-manager',) not in harness.events


def test_refresh_closes_its_reader_and_preserves_receipt_if_verification_raises(harness, monkeypatch):
    api, _, files, args, _ = original(harness); before = files.read(); closed = []
    reader = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(api.ReadOnlyClient, 'from_current_session', lambda *_a, **_k: reader)
    def failed(*_a, **_k): raise api.XhsReadbackError('safe_read_failure')
    monkeypatch.setattr(api, 'verify_ack', failed)
    module = harness.load('xhs_publish')
    with pytest.raises(api.XhsReadbackError): command(module)(args)
    assert closed == [True] and files.read() == before


def test_refresh_cli_routes_only_the_original_private_files_and_no_proxy(harness, monkeypatch):
    _, _, files, _, _ = original(harness); module = harness.load('xhs_publish'); seen = []
    assert callable(getattr(module, 'cmd_refresh_receipt', None)), 'Missing GET-only XHS receipt refresh command'
    monkeypatch.setattr(module, 'cmd_refresh_receipt', lambda args: seen.append(vars(args)) or 0)
    monkeypatch.setattr(sys, 'argv', ['xhs_publish.py', 'refresh-receipt', '--no-proxy',
        '--receipt-context', str(files.context_path), '--receipt-file', str(files.receipt_path)])
    assert module.main() == 0 and len(seen) == 1
    assert seen[0]['receipt_context'] == str(files.context_path) and seen[0]['no_proxy'] is True
    assert not any(key in seen[0] for key in ('title', 'video', 'images', 'exec', 'id', 'profile_base'))


@pytest.mark.parametrize('extra', [
    ['--title', 'another post'], ['--video', 'other.mp4'], ['--images', 'other.jpg'],
    ['--exec'], ['--id', NOTE], ['--profile-base', 'other-profile'], ['--proxy', 'http://other.invalid'],
])
def test_refresh_cli_rejects_content_profile_or_external_id_arguments(harness, monkeypatch, extra):
    module = harness.load('xhs_publish')
    assert callable(getattr(module, 'cmd_refresh_receipt', None)), 'Missing GET-only XHS receipt refresh command'
    monkeypatch.setattr(sys, 'argv', ['xhs_publish.py', 'refresh-receipt', *extra])
    with pytest.raises(SystemExit) as stopped: module.main()
    assert stopped.value.code == 2 and not harness.factories
