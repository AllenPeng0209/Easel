"""D30 evidence-only normalization; no network, browser or publisher invocation."""
from copy import deepcopy
import importlib
import uuid

import pytest


def api():
    return importlib.import_module('easel.publication_receipts')


def host(platform='douyin', mode='publish_now'):
    return {'operationId': 'a' * 32, 'payloadHash': 'b' * 64, 'platform': platform,
            'mode': mode, 'approvedAccountId': 'self-account'}


def receipt(platform='douyin', mode='publish_now', phase='platform_ack'):
    return {'kind': 'publication_receipt', 'schemaVersion': 1,
            **{key: value for key, value in host(platform, mode).items() if key != 'approvedAccountId'},
            'phase': phase, 'account': {'id': 'self-account', 'authenticated': True},
            'ack': {'kind': 'post', 'accepted': True, 'id': 'post-one',
                    'source': 'provider_response', 'state': 'processing'}}


def public_receipt():
    value = receipt(phase='readback')
    value['readback'] = {'outcome': 'verified', 'matched': {
        'platform_content_id': 'post-one', 'title': 'not used for identity',
        'status': 'published', 'published_at_ms': 1_001_000},
        'evidence': {'method': 'authenticated_readback', 'matchMethod': 'ack_id', 'unique': True,
                     'statusSource': 'explicit', 'visibility': 'public', 'baselineCaptured': True,
                     'sinceMs': 1_000_000, 'checkedAtMs': 1_002_000,
                     'account': {'id': 'self-account', 'authenticated': True}}}
    return value


@pytest.mark.parametrize('observation', [None, '✅ 发布成功 BVxxxx https://example.test/post',
    {'outcome': 'verified', 'matched': {'platform_content_id': 'post-one', 'status': 'published'}},
    {'success': True, 'media_id': 'draft-one'}])
def test_exit_zero_and_legacy_or_log_observation_never_confirm(observation):
    result = api().normalize_receipt(host(), observation, returncode=0)
    assert result['status'] == 'unknown'
    assert result['operationId'] == host()['operationId']
    assert 'url' not in result and 'stdout' not in result


@pytest.mark.parametrize('key,value', [('schemaVersion', 2), ('schemaVersion', True),
    ('operationId', 'c' * 32), ('payloadHash', 'c' * 64), ('platform', 'zhihu'), ('mode', 'create_draft')])
def test_version_or_correlation_conflict_is_rejected_not_rebound(key, value):
    module = api()
    observation = receipt()
    observation[key] = value
    with pytest.raises(module.PublicationReceiptError) as caught:
        module.normalize_receipt(host(), observation, returncode=0)
    assert caught.value.status_code == 409


def test_mp_provider_draft_ack_is_draft_only_not_publication():
    observation = receipt('wechat-oa', 'create_draft')
    observation['ack'] = {'kind': 'draft', 'accepted': True, 'id': 'draft-one',
                          'source': 'provider_response', 'thumb_media_id': 'cover-one'}
    result = api().normalize_receipt(host('wechat-oa', 'create_draft'), observation, returncode=0)
    assert result['status'] == 'draft_created'
    assert result['externalId'] == 'draft-one'
    assert result['publicationState'] == 'draft'
    assert 'thumb_media_id' not in result


@pytest.mark.parametrize('account', [None, {'id': 'self-account'}, {'authenticated': False}])
def test_mp_draft_ack_without_actual_account_is_limited_to_host_dispatch_identity(account):
    observation = receipt('wechat-oa', 'create_draft')
    observation['account'] = account
    observation['ack'].update(kind='draft', id='draft-one')
    result = api().normalize_receipt(host('wechat-oa', 'create_draft'), observation)
    assert result['status'] == 'draft_created'
    assert result['externalId'] == 'draft-one'
    assert result['identityVerification'] == 'host_dispatch_only'
    assert result['accountId'] == 'self-account'
    assert result['publicationState'] == 'draft'


@pytest.mark.parametrize('authenticated', [True, False])
def test_mp_draft_ack_explicit_different_actual_account_stays_unknown_with_draft_id(authenticated):
    observation = receipt('wechat-oa', 'create_draft')
    observation['account'] = {'id': 'another-account', 'authenticated': authenticated}
    observation['ack'].update(kind='draft', id='draft-one')
    result = api().normalize_receipt(host('wechat-oa', 'create_draft'), observation)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'account_mismatch'
    assert result['externalId'] == 'draft-one'
    assert result['accountId'] == 'self-account'
    assert result['identityVerification'] == 'account_mismatch'


def test_mp_draft_ack_relaxation_does_not_apply_to_publish_mode():
    observation = receipt('wechat-oa', 'publish_now')
    observation['account'] = None
    observation['ack'].update(kind='draft', id='draft-one')
    assert api().normalize_receipt(host('wechat-oa', 'publish_now'), observation)['status'] == 'unknown'


@pytest.mark.parametrize('ack', [
    {'kind': 'draft', 'accepted': True, 'thumb_media_id': 'cover-one', 'source': 'provider_response'},
    {'kind': 'draft', 'accepted': True, 'id': 'cover-one', 'source': 'cover_upload'},
    {'kind': 'media_upload', 'accepted': True, 'id': 'cover-one', 'source': 'provider_response'},
    {'kind': 'draft', 'accepted': False, 'id': 'draft-one', 'source': 'provider_response'},
])
def test_cover_or_missing_draft_id_cannot_become_draft_receipt(ack):
    observation = receipt('wechat-oa', 'create_draft')
    observation['ack'] = ack
    assert api().normalize_receipt(host('wechat-oa', 'create_draft'), observation)['status'] == 'unknown'


@pytest.mark.parametrize('state', ['processing', 'reviewing', 'private', 'scheduled', 'unknown', None])
def test_provider_ack_without_independent_public_readback_is_only_submitted(state):
    observation = receipt()
    observation['ack']['state'] = state
    result = api().normalize_receipt(host(), observation, returncode=0)
    assert result['status'] == 'submitted'
    assert result['externalId'] == 'post-one'


def test_strong_same_account_ack_and_independent_public_readback_can_confirm():
    result = api().normalize_receipt(host(), public_receipt(), returncode=0)
    assert result['status'] == 'confirmed'
    assert result['externalId'] == 'post-one'
    assert result['publicationState'] == 'public'
    assert result['evidenceMethod'] == 'authenticated_readback'


@pytest.mark.parametrize('field,value', [('unique', False), ('matchMethod', 'title_prefix'),
    ('statusSource', 'default'), ('visibility', 'private'), ('baselineCaptured', False),
    ('sinceMs', None), ('checkedAtMs', None)])
def test_legacy_verification_booleans_or_missing_strong_evidence_do_not_confirm(field, value):
    observation = public_receipt()
    observation['readback']['evidence'][field] = value
    assert api().normalize_receipt(host(), observation)['status'] == 'submitted'


@pytest.mark.parametrize('mutation', ['no-status', 'no-time', 'other-id', 'old-time', 'ambiguous', 'no-identity', 'other-account'])
def test_missing_or_conflicting_readback_does_not_turn_ack_into_public_success(mutation):
    observation = public_receipt()
    readback = observation['readback']
    if mutation == 'no-status':
        readback['matched'].pop('status')
    elif mutation == 'no-time':
        readback['matched']['published_at_ms'] = None
    elif mutation == 'other-id':
        readback['matched']['platform_content_id'] = 'other-post'
    elif mutation == 'old-time':
        readback['matched']['published_at_ms'] = 1
    elif mutation == 'ambiguous':
        readback['evidence']['unique'] = False
        readback['candidates'] = [readback['matched'], deepcopy(readback['matched'])]
    elif mutation == 'no-identity':
        readback['evidence'].pop('account')
    else:
        readback['evidence']['account']['id'] = 'another-account'
    assert api().normalize_receipt(host(), observation)['status'] == 'submitted'


@pytest.mark.parametrize('account', [None, {'id': 'self-account'}, {'id': 'another-account', 'authenticated': True}])
def test_unverified_or_wrong_submission_account_stays_unknown(account):
    observation = public_receipt()
    observation['account'] = account
    result = api().normalize_receipt(host(), observation)
    assert result['status'] == 'unknown'
    assert result['accountId'] == 'self-account'
    assert 'externalId' not in result


@pytest.mark.parametrize('reason', ['dry_run', 'help', 'validation_failed', 'runner_unavailable'])
def test_known_no_execution_is_not_started_not_a_submission(reason):
    result = api().normalize_receipt(host(), None, returncode=0, not_started_reason=reason)
    assert result['status'] == 'failed' and result['notStarted'] is True
    assert result['reason'] == reason


def test_submit_attempted_timeout_is_unknown_not_failed_or_retried():
    observation = receipt(phase='submit_attempted')
    observation.pop('ack')
    result = api().normalize_receipt(host(), observation, returncode=1, process_error='timeout')
    assert result['status'] == 'unknown'
    assert result['phase'] == 'submit_attempted'
    assert result['reason'] == 'timeout_after_submission'
    assert result.get('notStarted') is not True


def test_ack_survives_later_timeout_as_submitted_without_fabricating_publicity():
    result = api().normalize_receipt(host(), receipt(), process_error='timeout')
    assert result['status'] == 'submitted'


def test_result_is_whitelisted_and_does_not_echo_arbitrary_urls_cookies_or_error_text():
    observation = public_receipt()
    canary = 'fixture-' + uuid.uuid4().hex
    observation.update(cookie=canary, stdout=canary, url='https://example.test/' + canary, reason=canary)
    observation['readback']['evidence']['account']['cookie'] = canary
    result = api().normalize_receipt(host(), observation, process_error=canary)
    assert result['status'] == 'confirmed'
    assert canary not in str(result)
    assert set(result) <= {'status', 'reason', 'phase', 'operationId', 'payloadHash', 'platform', 'mode',
                           'accountId', 'externalId', 'publicationState', 'evidenceMethod', 'runnerExitCode', 'notStarted'}


def test_contradictory_not_started_host_marker_cannot_hide_correlated_provider_ack():
    module = api()
    with pytest.raises(module.PublicationReceiptError) as caught:
        module.normalize_receipt(host(), public_receipt(), not_started_reason='help')
    assert caught.value.status_code == 409


@pytest.mark.parametrize('identifier', ['', 0, '0', False, 'https://example.test/post', 'javascript:alert'])
def test_non_ids_and_url_strings_are_not_provider_receipts(identifier):
    observation = receipt('wechat-oa', 'create_draft')
    observation['ack'].update(kind='draft', id=identifier)
    result = api().normalize_receipt(host('wechat-oa', 'create_draft'), observation)
    assert result['status'] == 'unknown' and 'externalId' not in result


def test_duplicate_ack_id_candidates_do_not_confirm_even_if_legacy_unique_flag_is_true():
    observation = public_receipt()
    observation['readback']['candidates'] = [observation['readback']['matched'], deepcopy(observation['readback']['matched'])]
    assert api().normalize_receipt(host(), observation)['status'] == 'submitted'


def test_time_and_default_published_flags_cannot_substitute_for_explicit_public_status():
    observation = public_receipt()
    observation['readback']['matched']['status'] = {}
    observation['readback']['evidence']['statusSource'] = 'explicit'
    assert api().normalize_receipt(host(), observation)['status'] == 'submitted'


@pytest.mark.parametrize('state', ['rejected', 'prohibited'])
def test_explicit_same_ack_authenticated_rejection_is_failed_without_publication_timestamp(state):
    observation = public_receipt()
    observation['readback']['matched'].update(status=state, published_at_ms=None)
    observation['readback']['evidence'].update(visibility='private', baselineCaptured=False, sinceMs=None, checkedAtMs=None)
    result = api().normalize_receipt(host(), observation)
    assert result['status'] == 'failed'
    assert result['externalId'] == 'post-one'
    assert result['publicationState'] == state
    assert result['evidenceMethod'] == 'authenticated_readback'
    assert result.get('notStarted') is not True


@pytest.mark.parametrize('mutation', ['account', 'ack-id', 'method', 'title-match', 'ambiguous', 'default-status'])
def test_rejection_without_exact_independent_identity_or_id_proof_stays_submitted(mutation):
    observation = public_receipt()
    observation['readback']['matched'].update(status='rejected', published_at_ms=None)
    evidence = observation['readback']['evidence']
    if mutation == 'account':
        evidence['account']['id'] = 'another-account'
    elif mutation == 'ack-id':
        observation['readback']['matched']['platform_content_id'] = 'another-post'
    elif mutation == 'method':
        evidence['method'] = 'ui_toast'
    elif mutation == 'title-match':
        evidence['matchMethod'] = 'title_prefix'
    elif mutation == 'ambiguous':
        evidence['unique'] = False
    else:
        evidence['statusSource'] = 'default'
    assert api().normalize_receipt(host(), observation)['status'] == 'submitted'


@pytest.mark.parametrize('state', ['processing', 'reviewing', 'private'])
def test_explicit_nonpublic_state_does_not_require_publication_timestamp_to_display(state):
    observation = public_receipt()
    observation['ack']['state'] = 'unknown'
    observation['readback']['matched'].update(status=state, published_at_ms=None)
    observation['readback']['evidence']['visibility'] = 'private'
    result = api().normalize_receipt(host(), observation)
    assert result['status'] == 'submitted'
    assert result['publicationState'] == state
