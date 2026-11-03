"""D30 read-only evidence progression never obtains execution authority."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from easel.publication_approvals import PublicationApprovalError, PublicationApprovalStore


SCOPE = {'actor_id': 'actor-one', 'project_id': 'ip-one'}


def started(directory, *, mode='publish_now', actor='actor-one', body='body'):
    store = PublicationApprovalStore(directory)
    subject = {'project': {'id': 'ip-one'}, 'platform': {'id': 'douyin'},
               'account': {'id': 'remote-one'}, 'mode': mode, 'body': body}
    scope = {**SCOPE, 'actor_id': actor}
    record = store.create(subject, **scope)
    store.decide(record['id'], True, record['payloadHash'], **scope)
    return store, store.claim(record['id'], subject, **scope)


def result(record, status='submitted', **extra):
    value = {'status': status, 'operationId': record['operationId'], 'payloadHash': record['payloadHash'],
             'platform': record['subject']['platform']['id'], 'mode': record['subject']['mode'],
             'accountId': record['subject']['account']['id'], 'phase': 'platform_ack',
             'reason': 'provider_submission_acknowledged'}
    if isinstance(status, str) and status in {'submitted', 'scheduled', 'draft_created', 'confirmed'}:
        value.update(externalId='post-one', evidenceMethod='provider_response', publicationState='unknown')
    if status == 'confirmed':
        value.update(evidenceMethod='authenticated_readback', publicationState='public',
                     phase='readback', reason='public_readback_confirmed')
    if status == 'failed':
        value.update(notStarted=True, phase='not_started', reason='validation_failed')
    return {**value, **extra}


def ended(directory, *, mode='publish_now', status='unknown'):
    store, record = started(directory, mode=mode)
    store.finish(record['id'], record['operationId'], result(record, status), **SCOPE)
    return store, record


@pytest.mark.parametrize('status', ['submitted', 'confirmed', 'failed'])
def test_ended_unknown_can_progress_without_claiming_another_operation(tmp_path, status):
    store, record = ended(tmp_path)
    observed = store.observe(record['id'], record['operationId'], result(record, status), **SCOPE)
    assert observed['result']['status'] == status
    assert observed['operationId'] == record['operationId']
    assert store.claim(record['id'], record['subject'], **SCOPE)['claimed'] is False
    assert PublicationApprovalStore(tmp_path).get(record['id'], **SCOPE) == observed


def test_observing_running_operation_cannot_release_profile_lock(tmp_path):
    store, record = started(tmp_path)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, 'confirmed'), **SCOPE)
    assert caught.value.status_code == 409
    assert store.profile_busy('douyin', project_id='ip-one') is True
    assert store.get(record['id'], **SCOPE)['result'] == {'status': 'executing'}


def test_observe_does_not_disturb_another_actor_active_profile(tmp_path):
    store, record = ended(tmp_path)
    _, active = started(tmp_path, actor='actor-two', body='another')
    observed = store.observe(record['id'], record['operationId'], result(record, 'confirmed'), **SCOPE)
    assert observed['result']['status'] == 'confirmed'
    assert store.profile_busy('douyin', project_id='ip-one') is True
    assert store.get(active['id'], actor_id='actor-two', project_id='ip-one')['result']['status'] == 'executing'


@pytest.mark.parametrize('scope', [{'actor_id': 'another', 'project_id': 'ip-one'},
                                  {'actor_id': 'actor-one', 'project_id': 'another'}])
def test_observe_cannot_read_or_mutate_another_user_or_ip(tmp_path, scope):
    store, record = ended(tmp_path)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, 'confirmed'), **scope)
    assert caught.value.status_code == 404
    assert store.get(record['id'], **SCOPE)['result']['status'] == 'unknown'


@pytest.mark.parametrize('key', ['operationId', 'payloadHash', 'platform', 'mode', 'accountId'])
@pytest.mark.parametrize('missing', [False, True])
def test_observe_requires_complete_fixed_normalizer_correlation(tmp_path, key, missing):
    store, record = ended(tmp_path)
    evidence = result(record, 'confirmed')
    if missing:
        evidence.pop(key)
    else:
        evidence[key] = 'another'
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], evidence, **SCOPE)
    assert caught.value.status_code == 409
    assert store.get(record['id'], **SCOPE)['result']['status'] == 'unknown'


def test_wrong_operation_argument_cannot_be_rebound_by_receipt(tmp_path):
    store, record = ended(tmp_path)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], 'f' * 32, result(record, 'confirmed'), **SCOPE)
    assert caught.value.status_code == 409


def test_changed_external_id_cannot_swap_to_another_post(tmp_path):
    store, record = ended(tmp_path, status='submitted')
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, 'confirmed', externalId='another-post'), **SCOPE)
    assert caught.value.status_code == 409
    assert store.get(record['id'], **SCOPE)['result']['externalId'] == 'post-one'


@pytest.mark.parametrize('alias', ['external_id', 'external_message_id'])
def test_legacy_platform_id_alias_also_prevents_swapping_posts(tmp_path, alias):
    store, record = started(tmp_path)
    store.finish(record['id'], record['operationId'], {'status': 'submitted', alias: 'original-post'}, **SCOPE)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, 'confirmed', externalId='different-post'), **SCOPE)
    assert caught.value.status_code == 409
    observed = store.observe(record['id'], record['operationId'], result(record, 'confirmed', externalId='original-post'), **SCOPE)
    assert observed['result']['externalId'] == 'original-post'
    assert any(item['result'].get(alias) == 'original-post' for item in observed['observations'])


@pytest.mark.parametrize('changes', [{'externalId': None}, {'externalId': 'https://example.test/post'},
    {'evidenceMethod': 'provider_response'}, {'publicationState': 'private'}, {'verified': True, 'evidenceMethod': None}])
def test_weak_confirmation_cannot_promote_ended_result(tmp_path, changes):
    store, record = ended(tmp_path)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, 'confirmed', **changes), **SCOPE)
    assert caught.value.status_code == 409


@pytest.mark.parametrize('mode,status', [('create_draft', 'confirmed'), ('manual_export', 'confirmed'),
    ('publish_now', 'draft_created'), ('publish_now', 'exported')])
def test_observe_cannot_change_approved_effect_mode(tmp_path, mode, status):
    store, record = ended(tmp_path, mode=mode)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, status), **SCOPE)
    assert caught.value.status_code == 409


def test_draft_created_effect_can_be_recorded_without_publication_claim(tmp_path):
    store, record = ended(tmp_path, mode='create_draft')
    draft = result(record, 'draft_created', publicationState='draft', identityVerification='host_dispatch_only')
    assert store.observe(record['id'], record['operationId'], draft, **SCOPE)['result'] == draft


@pytest.mark.parametrize('initial', ['submitted', 'scheduled'])
def test_submission_or_schedule_can_advance_to_strong_public_confirmation(tmp_path, initial):
    store, record = ended(tmp_path, mode='scheduled' if initial == 'scheduled' else 'publish_now', status=initial)
    observed = store.observe(record['id'], record['operationId'], result(record, 'confirmed'), **SCOPE)
    assert observed['result']['status'] == 'confirmed'
    assert any(item['result']['status'] == initial for item in observed['observations'])


def test_weak_followup_preserves_confirmation_and_appends_durable_diagnostic(tmp_path):
    store, record = ended(tmp_path, status='submitted')
    confirmed = result(record, 'confirmed')
    first = store.observe(record['id'], record['operationId'], confirmed, **SCOPE)
    weak = result(record, 'unknown', reason='readback_time_unverified')
    second = store.observe(record['id'], record['operationId'], weak, **SCOPE)
    assert second['result'] == confirmed
    assert any(item['result'] == confirmed for item in second['observations'])
    assert any(item['result'] == weak for item in second['observations'])
    assert len(second['observations']) > len(first['observations'])
    assert PublicationApprovalStore(tmp_path).get(record['id'], **SCOPE) == second


def test_same_receipt_is_idempotent_across_restart_and_concurrent_observers(tmp_path):
    store, record = ended(tmp_path)
    evidence = result(record, 'confirmed')
    first = store.observe(record['id'], record['operationId'], evidence, **SCOPE)
    barrier = threading.Barrier(6, timeout=10)
    def observe(_):
        barrier.wait()
        return PublicationApprovalStore(tmp_path).observe(record['id'], record['operationId'], evidence, **SCOPE)
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(observe, range(6)))
    assert all(value == first for value in values)


def test_observe_rejects_executing_as_an_observation_not_new_execution_authority(tmp_path):
    store, record = ended(tmp_path)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, 'executing'), **SCOPE)
    assert caught.value.status_code == 409
    assert store.profile_busy('douyin', project_id='ip-one') is False


def test_diagnostics_are_whitelisted_and_cannot_persist_raw_cookies_or_stdout(tmp_path):
    store, record = ended(tmp_path)
    evidence = result(record, 'unknown', stdout='ignored', cookie='ignored', url='https://example.test/raw')
    observed = store.observe(record['id'], record['operationId'], evidence, **SCOPE)
    assert all('stdout' not in item['result'] and 'cookie' not in item['result'] and 'url' not in item['result']
               for item in observed['observations'])


@pytest.mark.parametrize('key', ['reason', 'phase', 'evidenceMethod', 'publicationState', 'identityVerification',
                                  'runnerExitCode', 'notStarted'])
def test_optional_diagnostics_cannot_smuggle_nested_raw_data(tmp_path, key):
    store, record = ended(tmp_path)
    evidence = result(record, 'unknown', **{key: {'stdout': 'must-not-persist'}})
    observed = store.observe(record['id'], record['operationId'], evidence, **SCOPE)
    assert 'must-not-persist' not in str(observed)


@pytest.mark.parametrize('key', ['runnerExitCode', 'notStarted'])
def test_typed_numeric_and_boolean_fields_cannot_store_arbitrary_strings(tmp_path, key):
    store, record = ended(tmp_path)
    evidence = result(record, 'unknown', **{key: 'must-not-persist'})
    observed = store.observe(record['id'], record['operationId'], evidence, **SCOPE)
    assert 'must-not-persist' not in str(observed)


@pytest.mark.parametrize('status', [[], {}, True, None])
def test_malformed_status_is_rejected_as_safe_validation_error(tmp_path, status):
    store, record = ended(tmp_path)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, status), **SCOPE)
    assert caught.value.status_code == 409


def test_failure_without_known_not_started_evidence_cannot_claim_failure(tmp_path):
    store, record = ended(tmp_path)
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, 'failed', notStarted=False), **SCOPE)
    assert caught.value.status_code == 409


def test_known_provider_submission_cannot_be_rewritten_as_not_started_failure(tmp_path):
    store, record = ended(tmp_path, status='submitted')
    with pytest.raises(PublicationApprovalError) as caught:
        store.observe(record['id'], record['operationId'], result(record, 'failed'), **SCOPE)
    assert caught.value.status_code == 409
    assert store.get(record['id'], **SCOPE)['result']['status'] == 'submitted'


@pytest.mark.parametrize('initial', ['unknown', 'submitted'])
@pytest.mark.parametrize('state', ['rejected', 'prohibited'])
def test_strong_same_object_provider_rejection_can_advance_to_failed(tmp_path, initial, state):
    store, record = ended(tmp_path, status=initial)
    evidence = result(record, 'failed', notStarted=False, phase='readback',
                      reason='provider_rejected', evidenceMethod='authenticated_readback',
                      publicationState=state, externalId='post-one')
    observed = store.observe(record['id'], record['operationId'], evidence, **SCOPE)
    assert observed['result']['status'] == 'failed'
    assert observed['result']['externalId'] == 'post-one'
    assert observed['result']['notStarted'] is False
    assert any(item['result']['status'] == initial for item in observed['observations'])


def test_late_platform_readback_cannot_erase_host_process_termination_facts(tmp_path):
    store, record = started(tmp_path)
    process = {'processState':'terminated', 'terminationConfirmed':True,
               'runnerTimedOut':False, 'outputTruncated':True}
    store.finish(record['id'], record['operationId'], result(record, 'unknown', **process), **SCOPE)
    observed = store.observe(record['id'], record['operationId'], result(record, 'confirmed',
        processState='stopping', terminationConfirmed=False), **SCOPE)
    assert observed['result']['status'] == 'confirmed'
    for key, value in process.items():
        assert observed['result'][key] == value
    # Receipt digests remain about platform evidence, not the host process facts.
    assert all(not any(key in item['result'] for key in process) for item in observed['observations'])
    replay = store.observe(record['id'], record['operationId'], result(record, 'confirmed'), **SCOPE)
    assert replay == observed
