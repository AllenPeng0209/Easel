"""Typed, private publisher observations; no platform or network calls."""
import importlib
import json
import os
import stat
from types import SimpleNamespace
import traceback

import pytest


def api():
    try:
        return importlib.import_module('skills.shared.scripts.publication_receipt')
    except ModuleNotFoundError:
        pytest.fail('The publication receipt writer has not been implemented')


def context():
    return {'operationId': 'a' * 32, 'payloadHash': 'b' * 64, 'platform': 'douyin',
            'mode': 'publish_now', 'approvedAccountId': 'user_id:self-123'}


@pytest.fixture
def files(tmp_path):
    root = tmp_path / 'private-operation'
    root.mkdir(mode=0o700)
    source, output = root / 'context.json', root / 'receipt.json'
    source.write_text(json.dumps(context()), encoding='utf-8')
    source.chmod(0o600)
    return source, output


def reporter(files):
    return api().PublicationReceiptReporter.from_args(
        SimpleNamespace(receipt_context=str(files[0]), receipt_file=str(files[1])), 'douyin')


def load(files):
    return json.loads(files[1].read_text(encoding='utf-8'))


def actual():
    return {'id': 'user_id:self-123', 'authenticated': True}


def ack():
    return {'kind': 'post', 'accepted': True, 'id': 'post-123',
            'source': 'provider_response', 'state': 'processing'}


def test_no_flags_is_disabled_and_has_no_io_side_effects(tmp_path):
    module = api()
    result = module.PublicationReceiptReporter.from_args(SimpleNamespace(), 'douyin')
    assert result.enabled is False
    assert result.record('submit_attempted', reason='legacy string') is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('name', ['receipt_context', 'receipt_file'])
def test_partial_flags_fail_closed_without_leaking_path(name, files):
    with pytest.raises(api().PublicationReceiptError) as caught:
        api().PublicationReceiptReporter.from_args(SimpleNamespace(**{name: str(files[0])}), 'douyin')
    assert str(files[0]) not in str(caught.value)
    assert not files[1].exists()


def test_atomic_private_envelope_never_invents_actual_account(files):
    writer = reporter(files)
    assert writer.enabled is True
    assert not files[1].exists()
    assert writer.record('not_started', reason='preflight_failed') is None
    value = load(files)
    assert value == {'kind': 'publication_receipt', 'schemaVersion': 1, **context(),
                     'phase': 'not_started', 'reason': 'preflight_failed'}
    assert stat.S_IMODE(files[1].stat().st_mode) == 0o600
    assert sorted(path.name for path in files[1].parent.iterdir()) == ['context.json', 'receipt.json']


@pytest.mark.parametrize('key,value', [('operationId', 'bad'), ('payloadHash', 'bad'),
    ('platform', 'bilibili'), ('mode', 'arbitrary secret'), ('approvedAccountId', 'nickname')])
def test_invalid_or_foreign_context_is_rejected_before_writing(files, key, value):
    data = context()
    data[key] = value
    files[0].write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(api().PublicationReceiptError):
        reporter(files)
    assert not files[1].exists()


def test_account_and_ack_are_evidence_not_context_copies_and_are_immutable(files):
    writer = reporter(files)
    writer.record('submit_attempted', account={**actual(), 'cookie': 'PRIVATE'})
    writer.record('platform_ack', ack={**ack(), 'response': {'cookie': 'PRIVATE'}, 'url': 'https://secret.test'})
    value = load(files)
    assert value['account'] == actual()
    assert value['ack'] == ack()
    assert 'PRIVATE' not in json.dumps(value)
    prior = files[1].read_bytes()
    with pytest.raises(api().PublicationReceiptError):
        writer.record('readback', account={'id': 'user_id:another', 'authenticated': True})
    with pytest.raises(api().PublicationReceiptError):
        writer.record('platform_ack', ack={**ack(), 'id': 'another-post'})
    assert files[1].read_bytes() == prior


@pytest.mark.parametrize('account', [{'id': 'nickname', 'authenticated': True},
    {'id': 'user_id:real'}, {'id': 'user_id:real', 'authenticated': 1},
    {'id': 'user_id:https://private.test', 'authenticated': True}])
def test_unproven_or_untyped_actual_identity_is_not_written(files, account):
    writer = reporter(files)
    with pytest.raises(api().PublicationReceiptError):
        writer.record('submit_attempted', account=account)
    assert not files[1].exists()


@pytest.mark.parametrize('phase', ['not_started', 'submit_attempted'])
def test_different_authenticated_actual_account_blocks_before_submission(files, phase):
    writer = reporter(files)
    with pytest.raises(api().PublicationReceiptError) as caught:
        writer.record(phase, account={'id': 'user_id:other', 'authenticated': True},
                      reason='account_mismatch')
    assert not files[1].exists()
    assert 'user_id:other' not in str(caught.value)


def test_preexisting_wrong_actual_account_cannot_bypass_submission_guard_by_omission(files):
    files[1].write_text(json.dumps({'kind': 'publication_receipt', 'schemaVersion': 1,
        **context(), 'phase': 'not_started',
        'account': {'id': 'user_id:other', 'authenticated': True}}), encoding='utf-8')
    files[1].chmod(0o600)
    before = files[1].read_bytes()
    writer = reporter(files)
    with pytest.raises(api().PublicationReceiptError):
        writer.record('submit_attempted')
    assert files[1].read_bytes() == before


def test_unknown_actual_account_does_not_copy_approved_identity_before_submission(files):
    writer = reporter(files)
    writer.record('not_started')
    writer.record('submit_attempted', account=None)
    assert load(files)['phase'] == 'submit_attempted'
    assert 'account' not in load(files)


def test_wrong_account_discovered_after_submission_is_retained_without_downgrade(files):
    writer = reporter(files)
    writer.record('submit_attempted')
    writer.record('platform_ack', ack=ack())
    writer = reporter(files)
    wrong = {'id': 'user_id:other', 'authenticated': True}
    writer.record('readback', account=wrong,
                  readback={'outcome': 'verified', 'evidence': {'account': wrong}},
                  reason='account_mismatch')
    value = load(files)
    assert value['phase'] == 'readback' and value['reason'] == 'account_mismatch'
    assert value['account'] == wrong and value['readback']['evidence']['account'] == wrong
    assert value['ack'] == ack()
    assert value['approvedAccountId'] == 'user_id:self-123'
    with pytest.raises(api().PublicationReceiptError):
        writer.record('not_started', reason='account_mismatch')
    assert load(files) == value


def test_conflicting_late_readback_account_does_not_overwrite_submission_identity(files):
    writer = reporter(files)
    writer.record('submit_attempted', account=actual())
    writer.record('platform_ack', ack=ack())
    wrong = {'id': 'user_id:other', 'authenticated': True}
    writer.record('readback', readback={'outcome': 'verified', 'evidence': {'account': wrong}},
                  reason='account_mismatch')
    value = load(files)
    assert value['account'] == actual()
    assert value['readback']['evidence']['account'] == wrong
    assert value['ack'] == ack() and value['phase'] == 'readback'


def test_readback_failure_preserves_provider_ack_across_reporter_reopen(files):
    writer = reporter(files)
    writer.record('platform_ack', account=actual(), ack=ack())
    writer = reporter(files)
    writer.record('readback', readback={'outcome': 'readback_error', 'error': 'cookie=SECRET'},
                  reason='readback_error')
    value = load(files)
    assert value['phase'] == 'readback'
    assert value['ack'] == ack() and value['account'] == actual()
    assert value['reason'] == 'readback_error'
    assert value['readback']['outcome'] == 'readback_error'
    assert 'SECRET' not in json.dumps(value)
    with pytest.raises(api().PublicationReceiptError):
        writer.record('not_started', reason='internal_error')
    assert load(files) == value


def test_real_readback_result_keeps_only_evidence_whitelist(files):
    readback = importlib.import_module('skills.shared.scripts.platform_readback')
    matched = readback.WorkItem('post-123', 'A title', 'published', 1001, {'cookie': 'SECRET'})
    evidence = {'method': 'authenticated_readback', 'account': {**actual(), 'headers': 'SECRET'},
        'matchMethod': 'ack_id', 'unique': True, 'statusSource': 'explicit', 'visibility': 'public',
        'baselineCaptured': True, 'sinceMs': 1000, 'checkedAtMs': 1002,
        'url': 'https://private.test/SECRET', 'raw': {'cookies': 'SECRET'}}
    result = readback.ReadbackResult('verified', matched, [matched], evidence, 'SECRET')
    writer = reporter(files)
    writer.record('readback', account=actual(), ack=ack(), readback=result)
    value = load(files)['readback']
    assert value['matched'] == {'platform_content_id': 'post-123', 'title': 'A title',
                                'status': 'published', 'published_at_ms': 1001}
    assert value['candidates'] == [value['matched']]
    assert value['evidence'] == {key: evidence[key] for key in evidence if key not in ('url', 'raw', 'account')} | {'account': actual()}
    assert 'SECRET' not in files[1].read_text()


def test_raw_readback_account_cannot_become_authenticated_or_submission_identity(files):
    writer = reporter(files)
    writer.record('readback', readback={'outcome': 'verified', 'evidence': {
        'account': {'platform_account_id': 'self-123', 'nickname': 'SECRET', 'cookie': 'SECRET'}}})
    value = load(files)
    assert 'account' not in value
    assert 'account' not in value['readback']['evidence']


@pytest.mark.parametrize('changes', [{'phase': 'finished'}, {'reason': 'cookie=SECRET'},
    {'ack': {**ack(), 'accepted': False}}, {'ack': {**ack(), 'source': 'ui_signal'}},
    {'ack': {**ack(), 'id': 'https://private.test/SECRET'}}])
def test_invalid_phase_reason_or_ack_cannot_leak_to_disk(files, changes):
    writer = reporter(files)
    arguments = {'phase': 'platform_ack', **changes}
    with pytest.raises(api().PublicationReceiptError) as caught:
        writer.record(**arguments)
    assert 'SECRET' not in str(caught.value)
    assert not files[1].exists()


@pytest.mark.parametrize('mutation', ['in_place', 'replace'])
def test_changed_context_is_rejected_without_rebinding(files, mutation):
    writer = reporter(files)
    data = context() | {'payloadHash': 'c' * 64}
    if mutation == 'replace':
        files[0].unlink()
    files[0].write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(api().PublicationReceiptError):
        writer.record('submit_attempted')
    assert not files[1].exists()


@pytest.mark.parametrize('target', ['context', 'receipt', 'parent'])
def test_symlink_paths_are_rejected_without_touching_target(tmp_path, files, target):
    source, output = files
    if target == 'context':
        alias = source.with_name('alias.json')
        alias.symlink_to(source)
        paths = alias, output
    elif target == 'receipt':
        output.symlink_to(source)
        paths = source, output
    else:
        alias = tmp_path / 'alias'
        alias.symlink_to(source.parent, target_is_directory=True)
        paths = alias / source.name, alias / output.name
    original = source.read_bytes()
    with pytest.raises(api().PublicationReceiptError):
        reporter(paths)
    assert source.read_bytes() == original


@pytest.mark.parametrize('hardlink', [False, True])
def test_receipt_never_overwrites_its_context(files, hardlink):
    source, output = files
    if hardlink:
        os.link(source, output)
    else:
        output = source
    original = source.read_bytes()
    with pytest.raises(api().PublicationReceiptError):
        reporter((source, output))
    assert source.read_bytes() == original


def test_replaced_parent_is_not_written_or_cleaned(files):
    writer = reporter(files)
    old_root = files[0].parent.with_name('detached')
    files[0].parent.rename(old_root)
    files[0].parent.mkdir(mode=0o700)
    files[0].write_text(json.dumps(context()), encoding='utf-8')
    files[1].write_text('replacement-owned', encoding='utf-8')
    with pytest.raises(api().PublicationReceiptError):
        writer.record('submit_attempted')
    assert files[1].read_text() == 'replacement-owned'
    assert not (old_root / 'receipt.json').exists()


@pytest.mark.parametrize('mutation', ['replace', 'in_place', 'symlink'])
def test_receipt_external_replacement_or_modification_is_not_overwritten(files, mutation):
    writer = reporter(files)
    writer.record('submit_attempted')
    if mutation != 'in_place':
        files[1].unlink()
    if mutation == 'symlink':
        files[1].symlink_to(files[0])
    else:
        files[1].write_text('replacement-owned', encoding='utf-8')
    with pytest.raises(api().PublicationReceiptError):
        writer.record('platform_ack', ack=ack())
    if mutation != 'symlink':
        assert files[1].read_text() == 'replacement-owned'
    assert json.loads(files[0].read_text()) == context()


def test_atomic_replace_failure_retains_old_receipt_and_cleans_private_temp(files, monkeypatch):
    writer = reporter(files)
    writer.record('submit_attempted')
    before = files[1].read_bytes()
    def fail(*args, **kwargs):
        raise OSError('SECRET private path')
    monkeypatch.setattr(api().os, 'replace', fail)
    with pytest.raises(api().PublicationReceiptError) as caught:
        writer.record('platform_ack', ack=ack())
    assert 'SECRET' not in str(caught.value)
    assert files[1].read_bytes() == before
    assert sorted(path.name for path in files[1].parent.iterdir()) == ['context.json', 'receipt.json']


def test_file_fsync_failure_never_replaces_prior_receipt(files, monkeypatch):
    writer = reporter(files)
    writer.record('submit_attempted')
    before = files[1].read_bytes()
    native = os.fsync
    def fail_file(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError('SECRET')
        return native(descriptor)
    monkeypatch.setattr(api().os, 'fsync', fail_file)
    with pytest.raises(api().PublicationReceiptError):
        writer.record('platform_ack', ack=ack())
    assert files[1].read_bytes() == before
    assert sorted(path.name for path in files[1].parent.iterdir()) == ['context.json', 'receipt.json']


def test_portable_late_root_replacement_does_not_clean_replacement_entries(files, monkeypatch):
    monkeypatch.setattr(api(), '_SAFE_DIR_FD', False)
    writer = reporter(files)
    root = files[0].parent
    detached = root.with_name('detached')
    native = os.fsync
    replacement_entry = None
    def replace_root(descriptor):
        nonlocal replacement_entry
        result = native(descriptor)
        if not detached.exists() and stat.S_ISREG(os.fstat(descriptor).st_mode):
            temporary = next(root.glob('.publication-receipt-*.tmp'))
            root.rename(detached)
            root.mkdir(mode=0o700)
            replacement_entry = root / temporary.name
            os.link(detached / temporary.name, replacement_entry)
        return result
    monkeypatch.setattr(api().os, 'fsync', replace_root)
    with pytest.raises(api().PublicationReceiptError):
        writer.record('submit_attempted')
    assert replacement_entry is not None and replacement_entry.exists()
    assert not files[1].exists()


def test_url_values_and_wrong_strong_evidence_types_are_not_persisted(files):
    writer = reporter(files)
    writer.record('readback', readback={'outcome': 'verified',
        'matched': {'platform_content_id': 'https://secret.test', 'title': 'https://secret.test',
                    'status': 'https://secret.test', 'published_at_ms': True},
        'evidence': {'method': 'https://secret.test', 'unique': 1, 'baselineCaptured': 'yes',
                     'sinceMs': True, 'checkedAtMs': '1000'}})
    value = load(files)['readback']
    assert value['matched'] == {}
    assert value['evidence'] == {}
    assert 'secret.test' not in files[1].read_text()


def test_foreign_existing_receipt_is_never_rebound(files):
    first = reporter(files)
    first.record('platform_ack', account=actual(), ack=ack())
    before = files[1].read_bytes()
    files[0].write_text(json.dumps(context() | {'operationId': 'c' * 32}), encoding='utf-8')
    with pytest.raises(api().PublicationReceiptError):
        reporter(files)
    assert files[1].read_bytes() == before


def test_public_error_traceback_does_not_chain_private_filesystem_error(files, monkeypatch):
    writer = reporter(files)
    sensitive = 'cookie-credential-and-private-host-path'
    def fail(*args, **kwargs):
        raise OSError(sensitive)
    monkeypatch.setattr(api().os, 'replace', fail)
    with pytest.raises(api().PublicationReceiptError) as caught:
        writer.record('submit_attempted')
    formatted = ''.join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert sensitive not in formatted


@pytest.mark.parametrize('mode', ['scheduled', 'manual_export'])
def test_host_execution_mode_is_preserved_without_claiming_submission(files, mode):
    files[0].write_text(json.dumps(context() | {'mode': mode}), encoding='utf-8')
    writer = reporter(files)
    writer.record('not_started', reason='dry_run')
    value = load(files)
    assert value['mode'] == mode
    assert value['phase'] == 'not_started' and 'ack' not in value


def host_files(files):
    module = api()
    assert hasattr(module, 'PublicationReceiptFiles'), 'Host publication receipt files are missing'
    return module.PublicationReceiptFiles(files[0].parent, context())


def test_host_files_publish_private_context_and_reporter_roundtrip(files):
    host = host_files(files)
    assert host.context_path == files[0].parent / 'receipt-context.json'
    assert host.receipt_path == files[0].parent / 'receipt.json'
    assert json.loads(host.context_path.read_text()) == context()
    assert stat.S_IMODE(host.context_path.stat().st_mode) == 0o600
    assert host.read() is None
    writer = api().PublicationReceiptReporter.from_args(SimpleNamespace(
        receipt_context=host.context_path, receipt_file=host.receipt_path), 'douyin')
    writer.record('submit_attempted', account=actual())
    assert host.read()['phase'] == 'submit_attempted'
    writer.record('platform_ack', ack=ack())
    observation = host.read()
    assert observation['ack'] == ack() and observation['account'] == actual()
    assert observation['payloadHash'] == 'b' * 64


def test_host_reopen_same_context_is_read_only_and_preserves_ack(files):
    host = host_files(files)
    writer = api().PublicationReceiptReporter.from_args(SimpleNamespace(
        receipt_context=host.context_path, receipt_file=host.receipt_path), 'douyin')
    writer.record('platform_ack', account=actual(), ack=ack())
    context_before = host.context_path.stat()
    receipt_before = host.receipt_path.read_bytes()
    reopened = host_files(files)
    assert reopened.read()['ack'] == ack()
    context_after = host.context_path.stat()
    assert (context_after.st_ino, context_after.st_mtime_ns, context_after.st_ctime_ns) == (
        context_before.st_ino, context_before.st_mtime_ns, context_before.st_ctime_ns)
    assert host.receipt_path.read_bytes() == receipt_before


def test_host_conflicting_context_is_409_and_not_overwritten(files):
    host = host_files(files)
    before = host.context_path.read_bytes()
    with pytest.raises(api().PublicationReceiptError) as caught:
        api().PublicationReceiptFiles(files[0].parent, context() | {'payloadHash': 'c' * 64})
    assert caught.value.status_code == 409
    assert host.context_path.read_bytes() == before


@pytest.mark.parametrize('mutation', ['same_bytes_replace', 'in_place', 'symlink', 'hardlink'])
def test_host_read_never_adopts_changed_context(files, mutation):
    host = host_files(files)
    original = host.context_path.read_bytes()
    if mutation == 'same_bytes_replace':
        replacement = host.context_path.with_name('new-context.json')
        replacement.write_bytes(original)
        os.replace(replacement, host.context_path)
    elif mutation == 'in_place':
        host.context_path.write_text(json.dumps(context() | {'operationId': 'c' * 32}))
    elif mutation == 'symlink':
        host.context_path.unlink()
        host.context_path.symlink_to(files[0])
    else:
        os.link(host.context_path, host.context_path.with_name('context-alias.json'))
    host.receipt_path.write_text(json.dumps({'kind': 'publication_receipt', 'phase': 'readback'}))
    with pytest.raises(api().PublicationReceiptError):
        host.read()


def test_host_read_rejects_replacement_operation_directory(files):
    host = host_files(files)
    root = files[0].parent
    root.rename(root.with_name('detached'))
    root.mkdir(mode=0o700)
    host.context_path.write_text(json.dumps(context()))
    host.receipt_path.write_text(json.dumps({'kind': 'publication_receipt', 'phase': 'readback'}))
    with pytest.raises(api().PublicationReceiptError):
        host.read()
    assert json.loads(host.receipt_path.read_text())['phase'] == 'readback'


@pytest.mark.parametrize('mutation', ['symlink', 'hardlink', 'directory', 'oversize', 'duplicate_keys'])
def test_host_read_rejects_unsafe_or_ambiguous_receipt(files, mutation):
    host = host_files(files)
    if mutation == 'symlink':
        host.receipt_path.symlink_to(host.context_path)
    elif mutation == 'hardlink':
        os.link(host.context_path, host.receipt_path)
    elif mutation == 'directory':
        host.receipt_path.mkdir()
    elif mutation == 'oversize':
        host.receipt_path.write_text('{"x":"' + 'x' * (256 * 1024) + '"}')
    else:
        host.receipt_path.write_text('{"phase":"submit_attempted","phase":"readback"}')
    with pytest.raises(api().PublicationReceiptError):
        host.read()


def test_host_files_reject_context_extra_fields_without_creating_private_file(files):
    module = api()
    assert hasattr(module, 'PublicationReceiptFiles'), 'Host publication receipt files are missing'
    with pytest.raises(module.PublicationReceiptError):
        module.PublicationReceiptFiles(files[0].parent, context() | {'body': 'PRIVATE'})
    assert not (files[0].parent / 'receipt-context.json').exists()


def test_host_context_atomic_failure_keeps_directory_clean(files, monkeypatch):
    module = api()
    assert hasattr(module, 'PublicationReceiptFiles'), 'Host publication receipt files are missing'
    def fail(*args, **kwargs):
        raise OSError('PRIVATE')
    monkeypatch.setattr(module.os, 'fsync', fail)
    with pytest.raises(module.PublicationReceiptError) as caught:
        module.PublicationReceiptFiles(files[0].parent, context())
    assert 'PRIVATE' not in str(caught.value)
    assert sorted(path.name for path in files[0].parent.iterdir()) == ['context.json']


@pytest.mark.parametrize('portable', [False, True])
@pytest.mark.parametrize('mutation', ['context', 'root'])
def test_host_read_rejects_replacement_during_receipt_parse(files, monkeypatch, portable, mutation):
    if portable:
        monkeypatch.setattr(api(), '_SAFE_DIR_FD', False)
    host = host_files(files)
    host.receipt_path.write_text(json.dumps({'kind': 'publication_receipt', 'phase': 'readback'}))
    native = api()._json
    def race(data):
        result = native(data)
        if mutation == 'context':
            replacement = host.context_path.with_name('replacement.json')
            replacement.write_bytes(host.context_path.read_bytes())
            os.replace(replacement, host.context_path)
        else:
            root = host.context_path.parent
            root.rename(root.with_name('detached'))
            root.mkdir(mode=0o700)
            host.context_path.write_text(json.dumps(context()))
            host.receipt_path.write_text('replacement-owned')
        return result
    monkeypatch.setattr(api(), '_json', race)
    with pytest.raises(api().PublicationReceiptError):
        host.read()
    if mutation == 'root':
        assert host.receipt_path.read_text() == 'replacement-owned'


def test_host_context_exclusive_publish_never_replaces_a_concurrent_context(files, monkeypatch):
    module = api()
    target = files[0].parent / 'receipt-context.json'
    foreign = context() | {'payloadHash': 'c' * 64}
    native = module.os.link
    def race(*args, **kwargs):
        target.write_text(json.dumps(foreign))
        return native(*args, **kwargs)
    monkeypatch.setattr(module.os, 'link', race)
    with pytest.raises(module.PublicationReceiptError) as caught:
        module.PublicationReceiptFiles(files[0].parent, context())
    assert caught.value.status_code == 409
    assert json.loads(target.read_text()) == foreign
    assert not list(target.parent.glob('.publication-receipt-*.tmp'))


def test_host_reopen_rejects_duplicate_context_keys_without_overwrite(files):
    target = files[0].parent / 'receipt-context.json'
    original = json.dumps(context()).replace('"mode": "publish_now"',
                                            '"mode": "create_draft", "mode": "publish_now"')
    target.write_text(original)
    with pytest.raises(api().PublicationReceiptError):
        host_files(files)
    assert target.read_text() == original


def test_write_authority_requires_approved_account_even_after_an_attempt(files):
    writer = reporter(files)
    writer.record('submit_attempted')
    before = files[1].read_bytes()
    wrong = {'id': 'user_id:other', 'authenticated': True}
    for account in (None, wrong, {'id': actual()['id'], 'authenticated': False}):
        with pytest.raises(api().PublicationReceiptError):
            writer.require_approved_account(account)
    writer.require_approved_account(actual())
    assert files[1].read_bytes() == before
    # Observations of an already attempted submission still preserve provider evidence.
    writer.record('readback', account=wrong)
    assert load(files)['account'] == wrong


def test_write_authority_revalidates_private_approval_context(files):
    writer = reporter(files)
    files[0].write_text(json.dumps({**context(), 'payloadHash': 'd' * 64}), encoding='utf-8')
    with pytest.raises(api().PublicationReceiptError):
        writer.require_approved_account(actual())
    assert not files[1].exists()
