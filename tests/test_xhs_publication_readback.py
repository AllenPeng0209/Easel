"""A public XHS receipt needs the original ACK and two authenticated sources."""
from copy import deepcopy
import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


UID = 'approved-xhs'
ACCOUNT = {'id': 'userId:' + UID, 'authenticated': True}
CONTEXT = {'operationId': 'a' * 32, 'payloadHash': 'b' * 64,
           'platform': 'xiaohongshu', 'mode': 'publish_now',
           'approvedAccountId': ACCOUNT['id']}
NOTE = '6ad86a4600000000330118ff'
OLD = '6a25285c0000000006036c1b'
SINCE = 1_793_880_000_000
STAMP = {'xhsSessionGeneration': 2, 'xhsNativeAccountId': 1, 'xhsSessionHash': 'c' * 64}


def api():
    assert importlib.util.find_spec('easel.xhs_publication_readback') is not None, 'Missing XHS two-source readback'
    return importlib.import_module('easel.xhs_publication_readback')


def creator(identifier=NOTE, **changes):
    return {'id': identifier, 'permission_code': 0, 'tab_status': 1,
            'time': '2026-11-05 10:00', **changes}


def main(identifier=NOTE, **changes):
    return {'note_id': identifier, 'user': {'user_id': UID}, 'time': SINCE + 400, **changes}


class Reader:
    def __init__(self, *, creators=None, mains=None, identities=None, stamp=None):
        self.creators = [[creator(OLD)], [creator()]] if creators is None else creators
        self.mains = [[main(OLD, time=SINCE - 1_000_000)], [main()]] if mains is None else mains
        self.identities = [{'userId': UID}] * 4 if identities is None else identities
        self.snapshot_stamp = deepcopy(STAMP if stamp is None else stamp)
        self.calls = []

    def identity(self):
        self.calls.append('identity')
        return deepcopy(self.identities.pop(0))

    def creator_notes(self):
        self.calls.append('creator')
        return deepcopy(self.creators.pop(0))

    def own_notes(self, uid):
        assert uid == UID
        self.calls.append('main')
        return deepcopy(self.mains.pop(0))


def ack(identifier=NOTE):
    return {'kind': 'post', 'accepted': True, 'source': 'provider_response',
            'id': identifier, 'state': 'submitted'}


def observation(reader=None):
    module = api(); reader = reader or Reader()
    baseline = module.capture_baseline(reader, CONTEXT, now_ms=lambda: SINCE)
    proof = module.ack_baseline_evidence(baseline, ack(), CONTEXT, now_ms=lambda: SINCE + 500)
    value = {'kind': 'publication_receipt', 'schemaVersion': 1, **CONTEXT,
             'phase': 'platform_ack', 'account': ACCOUNT, 'ack': ack(), 'readback': proof}
    return reader, baseline, value


def normalize(value, readback):
    from easel.publication_receipts import normalize_receipt
    return normalize_receipt(CONTEXT, {**value, 'phase': 'readback', 'readback': readback})


def private_reporter(directory):
    spec = importlib.util.spec_from_file_location('xhs_real_receipt_writer',
        Path(__file__).parents[1] / 'skills/shared/scripts/publication_receipt.py')
    writer = importlib.util.module_from_spec(spec); spec.loader.exec_module(writer)
    files = writer.PublicationReceiptFiles(directory, CONTEXT)
    reporter = writer.PublicationReceiptReporter.from_args(SimpleNamespace(
        receipt_context=str(files.context_path), receipt_file=str(files.receipt_path)), 'xiaohongshu')
    return writer, files, reporter


def test_two_source_same_id_explicit_public_author_and_new_time_can_confirm():
    reader, baseline, value = observation()
    result = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    normalized = normalize(value, result)
    assert normalized['status'] == 'confirmed'
    assert normalized['externalId'] == NOTE and normalized['accountId'] == ACCOUNT['id']
    assert result['matched']['published_at_ms'] == SINCE + 400
    assert baseline.note_ids == frozenset({OLD})
    assert result['evidence']['visibility'] == 'public'
    assert all(result['evidence'][key] == value for key, value in STAMP.items())


def test_before_ack_private_proof_does_not_claim_an_ack_is_new():
    module = api(); reader = Reader()
    baseline = module.capture_baseline(reader, CONTEXT, now_ms=lambda: SINCE)
    before = module.baseline_evidence(baseline, CONTEXT, now_ms=lambda: SINCE)
    assert before['evidence']['sinceMs'] == SINCE
    assert before['evidence']['baselineCaptured'] is False
    after = module.ack_baseline_evidence(baseline, ack(), CONTEXT, now_ms=lambda: SINCE + 500)
    assert after['evidence']['baselineCaptured'] is True
    assert module.ack_baseline_evidence(baseline, ack(OLD), CONTEXT,
                                      now_ms=lambda: SINCE + 500)['evidence']['baselineCaptured'] is False


@pytest.mark.parametrize('changes', [
    {'permission_code': 4, 'permission_msg': '仅互关好友可见'},
    {'permission_code': 1}, {'tab_status': 2}, {'tab_status': 3}, {'tab_status': 4},
    {'permission_code': False}, {'permission_code': '0'}, {'tab_status': True},
    {'tab_status': '1'}, {'permission_code': None}, {'tab_status': None},
])
def test_published_is_not_public_and_typed_or_incomplete_state_never_confirms(changes):
    reader = Reader(creators=[[creator(OLD)], [creator(**changes)]])
    reader, _, value = observation(reader)
    result = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    assert normalize(value, result)['status'] != 'confirmed'


@pytest.mark.parametrize('item', [
    main(user={'user_id': 'different'}), main(user={}), main(user=None),
    main(time=1793880000), main(time=str(SINCE + 400)), main(time=True),
    main(time=SINCE - 2000), main(time=SINCE + 20_000),
])
def test_creator_metadata_cannot_invent_an_author_or_millisecond_publication_time(item):
    reader = Reader(mains=[[main(OLD)], [item]])
    reader, _, value = observation(reader)
    result = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    assert normalize(value, result)['status'] != 'confirmed'


@pytest.mark.parametrize('which', ['creator-missing', 'main-missing', 'creator-duplicate', 'main-duplicate'])
def test_exact_ack_id_must_be_unique_in_both_sources(which):
    reader = Reader()
    if which == 'creator-missing': reader.creators[1] = [creator(OLD)]
    if which == 'main-missing': reader.mains[1] = [main(OLD)]
    if which == 'creator-duplicate': reader.creators[1] *= 2
    if which == 'main-duplicate': reader.mains[1] *= 2
    reader, _, value = observation(reader)
    result = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    assert normalize(value, result)['status'] != 'confirmed'
    assert result['evidence']['unique'] is False


@pytest.mark.parametrize('mutation', ['operation', 'hash', 'account', 'no-baseline', 'no-ack', 'ambiguous',
    'bad-ambiguity-type', 'baseline-account', 'baseline-method', 'baseline-match', 'old-id', 'generation', 'source'])
def test_original_intent_baseline_and_session_are_required_before_any_refresh_query(mutation):
    reader, _, value = observation()
    if mutation == 'operation': value['operationId'] = 'd' * 32
    if mutation == 'hash': value['payloadHash'] = 'd' * 64
    if mutation == 'account': value['account'] = {'id': 'userId:other', 'authenticated': True}
    if mutation == 'no-baseline': value['readback']['evidence']['baselineCaptured'] = False
    if mutation == 'no-ack': value.pop('ack')
    if mutation == 'ambiguous': value['submissionAmbiguous'] = True
    if mutation == 'bad-ambiguity-type': value['submissionAmbiguous'] = 'true'
    if mutation == 'baseline-account': value['readback']['evidence']['account']['id'] = 'userId:other'
    if mutation == 'baseline-method': value['readback']['evidence']['method'] = 'legacy_dom'
    if mutation == 'baseline-match': value['readback']['evidence']['matchMethod'] = 'title'
    if mutation == 'old-id':
        value['ack']['id'] = OLD
        value['readback']['evidence']['baselineCaptured'] = False
    if mutation == 'generation': reader.snapshot_stamp['xhsSessionGeneration'] += 1
    if mutation == 'source': reader.snapshot_stamp['xhsSessionHash'] = 'd' * 64
    reader.calls.clear()
    result = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    assert result['outcome'] != 'verified' and reader.calls == []


@pytest.mark.parametrize('identities', [
    [{'userId': UID}, {'userId': 'other'}],
    [{'userId': ''}], [{'userId': False}], [None],
])
def test_baseline_fails_if_authenticated_creator_identity_is_missing_or_changes(identities):
    module = api(); reader = Reader(identities=identities)
    with pytest.raises(module.XhsReadbackError):
        module.capture_baseline(reader, CONTEXT, now_ms=lambda: SINCE)


def test_transport_exceptions_never_expose_provider_data_or_become_public_success():
    reader, _, value = observation()
    def broken(): raise RuntimeError('secret-cookie https://private.invalid/access-token')
    reader.creator_notes = broken
    result = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    assert result['outcome'] == 'readback_error'
    assert 'secret-cookie' not in repr(result) and 'private.invalid' not in repr(result)


def test_baseline_is_immutable_and_cannot_be_rebound_to_another_host_payload():
    module = api(); reader = Reader()
    baseline = module.capture_baseline(reader, CONTEXT, now_ms=lambda: SINCE)
    with pytest.raises((AttributeError, TypeError)):
        baseline.since_ms = SINCE - 1
    with pytest.raises(module.XhsReadbackError):
        module.ack_baseline_evidence(baseline, ack(), {**CONTEXT, 'payloadHash': 'd' * 64},
                                   now_ms=lambda: SINCE + 1)


def test_real_private_writer_retains_snapshot_binding_and_generic_normalizer_still_requires_public_proof(tmp_path):
    spec = importlib.util.spec_from_file_location('xhs_real_receipt_writer',
        Path(__file__).parents[1] / 'skills/shared/scripts/publication_receipt.py')
    writer = importlib.util.module_from_spec(spec); spec.loader.exec_module(writer)
    PublicationReceiptFiles, PublicationReceiptReporter = writer.PublicationReceiptFiles, writer.PublicationReceiptReporter
    files = PublicationReceiptFiles(tmp_path, CONTEXT)
    reporter = PublicationReceiptReporter.from_args(SimpleNamespace(
        receipt_context=str(files.context_path), receipt_file=str(files.receipt_path)), 'xiaohongshu')
    reader = Reader(); baseline = api().capture_baseline(reader, CONTEXT, now_ms=lambda: SINCE)
    reporter.record('submit_attempted', account=ACCOUNT,
        readback=api().baseline_evidence(baseline, CONTEXT, now_ms=lambda: SINCE))
    reporter.record('platform_ack', account=ACCOUNT, ack=ack(),
        readback=api().ack_baseline_evidence(baseline, ack(), CONTEXT, now_ms=lambda: SINCE + 500))
    observed = files.read()
    assert all(observed['readback']['evidence'].get(key) == value for key, value in STAMP.items())
    proof = api().verify_ack(reader, CONTEXT, observed, now_ms=lambda: SINCE + 1000)
    reporter.record('readback', account=ACCOUNT, readback=proof)
    from easel.publication_receipts import normalize_receipt
    assert normalize_receipt(CONTEXT, files.read())['status'] == 'confirmed'


@pytest.mark.parametrize('permission', [4, 999, -1])
def test_unpublic_or_unknown_permission_is_not_invented_as_private(permission):
    reader = Reader(creators=[[creator(OLD)], [creator(permission_code=permission)]])
    reader, _, value = observation(reader)
    proof = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    assert proof['outcome'] == 'unverified'


@pytest.mark.parametrize('when', [SINCE - 2000, SINCE + 20_000])
def test_helper_does_not_label_stale_or_future_public_evidence_verified(when):
    reader = Reader(mains=[[main(OLD)], [main(time=when)]])
    reader, _, value = observation(reader)
    proof = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    assert proof['outcome'] == 'unverified'


@pytest.mark.parametrize('key', list(STAMP))
def test_host_does_not_confirm_xhs_generic_public_claim_without_snapshot_evidence(key):
    reader, _, value = observation()
    proof = api().verify_ack(reader, CONTEXT, value, now_ms=lambda: SINCE + 1000)
    proof['evidence'].pop(key)
    assert normalize(value, proof)['status'] != 'confirmed'


def test_readonly_refresh_admission_requires_original_baseline_before_loading_any_client():
    _, _, value = observation()
    assert api().refresh_evidence_ready(CONTEXT, value, now_ms=lambda: SINCE + 1000)
    value['readback']['evidence'].pop('xhsSessionHash')
    assert not api().refresh_evidence_ready(CONTEXT, value, now_ms=lambda: SINCE + 1000)


@pytest.mark.parametrize('field,value', [
    ('sinceMs', SINCE - 10000), ('xhsSessionGeneration', 3),
    ('xhsNativeAccountId', 2), ('xhsSessionHash', 'd' * 64),
])
def test_xhs_private_writer_cannot_rebind_its_preclick_baseline(tmp_path, field, value):
    writer, files, reporter = private_reporter(tmp_path)
    baseline = api().capture_baseline(Reader(), CONTEXT, now_ms=lambda: SINCE)
    proof = api().baseline_evidence(baseline, CONTEXT, now_ms=lambda: SINCE)
    reporter.record('submit_attempted', account=ACCOUNT, readback=proof)
    original = files.receipt_path.read_bytes()
    changed = deepcopy(proof); changed['evidence'][field] = value
    with pytest.raises(writer.PublicationReceiptError):
        reporter.record('submit_attempted', account=ACCOUNT, readback=changed)
    assert files.receipt_path.read_bytes() == original


def test_successful_baseline_cannot_be_constructed_after_an_existing_ack(tmp_path):
    writer, files, reporter = private_reporter(tmp_path)
    reporter.record('submit_attempted', account=ACCOUNT)
    reporter.record('platform_ack', account=ACCOUNT, ack=ack())
    original = files.receipt_path.read_bytes()
    baseline = api().capture_baseline(Reader(), CONTEXT, now_ms=lambda: SINCE)
    late = api().ack_baseline_evidence(baseline, ack(), CONTEXT, now_ms=lambda: SINCE + 500)
    with pytest.raises(writer.PublicationReceiptError):
        reporter.record('readback', account=ACCOUNT, readback=late)
    assert files.receipt_path.read_bytes() == original


def test_old_ack_cannot_later_acquire_the_new_ack_flag(tmp_path):
    writer, files, reporter = private_reporter(tmp_path)
    baseline = api().capture_baseline(Reader(), CONTEXT, now_ms=lambda: SINCE)
    reporter.record('submit_attempted', account=ACCOUNT,
        readback=api().baseline_evidence(baseline, CONTEXT, now_ms=lambda: SINCE))
    proof = api().ack_baseline_evidence(baseline, ack(OLD), CONTEXT, now_ms=lambda: SINCE + 500)
    reporter.record('platform_ack', account=ACCOUNT, ack=ack(OLD), readback=proof)
    original = files.receipt_path.read_bytes()
    proof['evidence']['baselineCaptured'] = True
    with pytest.raises(writer.PublicationReceiptError):
        reporter.record('readback', account=ACCOUNT, readback=proof)
    assert files.receipt_path.read_bytes() == original
