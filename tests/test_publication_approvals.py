"""B30 durable, exact-payload approval and one-shot execution authority."""
import hashlib
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest


def api():
    return importlib.import_module('easel.publication_approvals')


def subject(**changes):
    value = {'schemaVersion': 1, 'project': {'id': 'ip-one', 'name': '知识 IP'},
             'platform': {'id': 'douyin', 'name': '抖音'},
             'account': {'id': 'remote-one', 'name': '目标账号'},
             'mode': 'publish_now', 'scheduledAt': None, 'audience': 'public',
             'title': '标题', 'body': '具体正文', 'tags': ['知识'], 'persona': '', 'media': []}
    return {**value, **changes}


def store_for(path):
    return api().PublicationApprovalStore(path)


def approved(store, value=None, *, actor='actor-one', project='ip-one'):
    record = store.create(value or subject(), actor_id=actor, project_id=project)
    return store.decide(record['id'], True, record['payloadHash'], actor_id=actor, project_id=project)


def test_subject_is_canonical_bound_and_persistent_without_approval_implying_execution(tmp_path):
    store = store_for(tmp_path)
    value = subject()
    record = store.create(value, actor_id='actor-one', project_id='ip-one')
    expected = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                        allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    assert record['payloadHash'] == expected
    assert record['state'] == 'pending'
    assert record['operationId'] is None and record['result'] is None
    assert record['actorId'] == 'actor-one' and record['projectId'] == 'ip-one'
    assert record['expiresAt'] - record['createdAt'] == 900_000
    assert store_for(tmp_path).get(record['id'], actor_id='actor-one', project_id='ip-one') == record
    reordered = store.create(dict(reversed(list(value.items()))), actor_id='actor-one', project_id='ip-one')
    assert reordered['payloadHash'] == record['payloadHash']


@pytest.mark.parametrize('extra', [float('nan'), float('inf'), {1: 'not a JSON key'}])
def test_noncanonical_json_is_rejected_without_creating_a_grant(tmp_path, extra):
    module = api()
    with pytest.raises(module.PublicationApprovalError) as caught:
        store_for(tmp_path).create(subject(extra=extra), actor_id='actor-one', project_id='ip-one')
    assert caught.value.status_code == 400


@pytest.mark.parametrize('method', ['get', 'decide', 'claim', 'finish'])
@pytest.mark.parametrize('scope', [('other-actor', 'ip-one'), ('actor-one', 'other-ip')])
def test_another_actor_or_ip_cannot_read_decide_claim_or_finish(tmp_path, method, scope):
    module = api()
    store = store_for(tmp_path)
    record = approved(store)
    claim = store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')
    kwargs = {'actor_id': scope[0], 'project_id': scope[1]}
    with pytest.raises(module.PublicationApprovalError) as caught:
        if method == 'get':
            store.get(record['id'], **kwargs)
        elif method == 'decide':
            store.decide(record['id'], True, record['payloadHash'], **kwargs)
        elif method == 'claim':
            store.claim(record['id'], subject(), **kwargs)
        else:
            store.finish(record['id'], claim['operationId'], {'status': 'unknown'}, **kwargs)
    assert caught.value.status_code == 404


def test_decision_is_idempotent_but_cannot_be_reversed_or_rebound(tmp_path):
    module = api()
    store = store_for(tmp_path)
    record = approved(store)
    assert store.decide(record['id'], True, record['payloadHash'], actor_id='actor-one', project_id='ip-one') == record
    with pytest.raises(module.PublicationApprovalError):
        store.decide(record['id'], False, record['payloadHash'], actor_id='actor-one', project_id='ip-one')
    pending = store.create(subject(), actor_id='actor-one', project_id='ip-one')
    with pytest.raises(module.PublicationApprovalError) as caught:
        store.decide(pending['id'], True, '0' * 64, actor_id='actor-one', project_id='ip-one')
    assert caught.value.status_code == 409
    assert store.get(pending['id'], actor_id='actor-one', project_id='ip-one')['state'] == 'invalidated'


def test_expiry_never_approves_and_cannot_resurrect_after_clock_moves_back(tmp_path, monkeypatch):
    module = api()
    clock = [1_700_000_000_000]
    monkeypatch.setattr(module, '_now_ms', lambda: clock[0])
    store = store_for(tmp_path)
    record = store.create(subject(), actor_id='actor-one', project_id='ip-one', ttl_seconds=1)
    clock[0] += 1001
    assert store.get(record['id'], actor_id='actor-one', project_id='ip-one')['state'] == 'expired'
    clock[0] -= 2000
    with pytest.raises(module.PublicationApprovalError):
        store.decide(record['id'], True, record['payloadHash'], actor_id='actor-one', project_id='ip-one')
    assert store.get(record['id'], actor_id='actor-one', project_id='ip-one')['state'] == 'expired'


def test_claim_requires_exact_approved_payload_and_invalidation_is_durable(tmp_path):
    module = api()
    store = store_for(tmp_path)
    pending = store.create(subject(), actor_id='actor-one', project_id='ip-one')
    with pytest.raises(module.PublicationApprovalError):
        store.claim(pending['id'], subject(), actor_id='actor-one', project_id='ip-one')
    record = approved(store)
    with pytest.raises(module.PublicationApprovalError) as caught:
        store.claim(record['id'], subject(body='改过的正文'), actor_id='actor-one', project_id='ip-one')
    assert caught.value.status_code == 409
    loaded = store_for(tmp_path).get(record['id'], actor_id='actor-one', project_id='ip-one')
    assert loaded['state'] == 'invalidated' and loaded['operationId'] is None
    with pytest.raises(module.PublicationApprovalError):
        store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')


def test_concurrent_claim_has_one_durable_operation_and_replays_after_restart(tmp_path):
    store = store_for(tmp_path)
    record = approved(store)
    barrier = threading.Barrier(8, timeout=10)
    def claim(_):
        barrier.wait()
        return store_for(tmp_path).claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(claim, range(8)))
    assert sum(value['claimed'] for value in values) == 1
    assert len({value['operationId'] for value in values}) == 1
    assert all(value['state'] == 'approved' and value['result']['status'] == 'executing' for value in values)
    assert store_for(tmp_path).claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')['claimed'] is False


def test_platform_profile_is_exclusive_across_actors_until_execution_settles(tmp_path):
    module = api()
    one, two = store_for(tmp_path), store_for(tmp_path)
    first = approved(one)
    second = approved(two, subject(body='另一个发布'), actor='actor-two')
    started = one.claim(first['id'], subject(), actor_id='actor-one', project_id='ip-one')
    with pytest.raises(module.PublicationApprovalError) as caught:
        two.claim(second['id'], subject(body='另一个发布'), actor_id='actor-two', project_id='ip-one')
    assert caught.value.status_code == 409
    assert 'actor-one' not in str(caught.value)
    one.finish(first['id'], started['operationId'], {'status': 'unknown'}, actor_id='actor-one', project_id='ip-one')
    assert two.claim(second['id'], subject(body='另一个发布'), actor_id='actor-two', project_id='ip-one')['claimed'] is True
    assert one.claim(first['id'], subject(), actor_id='actor-one', project_id='ip-one')['claimed'] is False


def test_finish_requires_matching_operation_and_does_not_promote_exit_zero(tmp_path):
    module = api()
    store = store_for(tmp_path)
    record = approved(store)
    claimed = store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')
    with pytest.raises(module.PublicationApprovalError):
        store.finish(record['id'], 'wrong-operation', {'status': 'confirmed', 'receiptId': 'remote'},
                     actor_id='actor-one', project_id='ip-one')
    settled = store.finish(record['id'], claimed['operationId'], {'exitCode': 0, 'ok': True},
                           actor_id='actor-one', project_id='ip-one')
    assert settled['state'] == 'approved'
    assert settled['result']['status'] == 'unknown'
    assert store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')['claimed'] is False


@pytest.mark.parametrize('result,want', [
    ({'status': 'confirmed'}, 'unknown'),
    ({'status': 'confirmed', 'receiptId': 'platform-post'}, 'confirmed'),
    ({'status': 'draft_created', 'externalId': 'draft-one'}, 'draft_created'),
    ({'status': 'exported'}, 'exported'),
])
def test_result_status_is_separate_from_approval_and_remains_persistent(tmp_path, result, want):
    store = store_for(tmp_path)
    record = approved(store)
    claimed = store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')
    settled = store.finish(record['id'], claimed['operationId'], result, actor_id='actor-one', project_id='ip-one')
    assert settled['state'] == 'approved' and settled['result']['status'] == want
    assert store_for(tmp_path).get(record['id'], actor_id='actor-one', project_id='ip-one') == settled
    assert store.finish(record['id'], claimed['operationId'], result, actor_id='actor-one', project_id='ip-one') == settled


def test_native_call_lookup_and_create_replay_original_record_before_any_new_capture(tmp_path):
    store = store_for(tmp_path)
    key, request_hash = 'session:turn:call', hashlib.sha256(b'original-tool-input').hexdigest()
    assert store.lookup_call(key, request_hash, actor_id='actor-one', project_id='ip-one') is None
    first = store.create(subject(), actor_id='actor-one', project_id='ip-one',
                         idempotency_key=key, request_hash=request_hash)
    retried = store_for(tmp_path).lookup_call(key, request_hash, actor_id='actor-one', project_id='ip-one')
    assert retried == first
    # A later host capture could contain a different generated artifact ID. The
    # accepted native call still owns its original subject and approval card.
    assert store.create(subject(body='later-generated'), actor_id='actor-one', project_id='ip-one',
                        idempotency_key=key, request_hash=request_hash) == first
    assert store.lookup_call(key, request_hash, actor_id='another-actor', project_id='ip-one') is None


def test_same_native_call_cannot_be_rebound_to_changed_raw_arguments(tmp_path):
    module = api()
    store = store_for(tmp_path)
    first_hash, changed_hash = hashlib.sha256(b'first').hexdigest(), hashlib.sha256(b'changed').hexdigest()
    record = store.create(subject(), actor_id='actor-one', project_id='ip-one',
                          idempotency_key='same-call', request_hash=first_hash)
    with pytest.raises(module.PublicationApprovalError) as caught:
        store.lookup_call('same-call', changed_hash, actor_id='actor-one', project_id='ip-one')
    assert caught.value.status_code == 409
    with pytest.raises(module.PublicationApprovalError):
        store.create(subject(body='changed'), actor_id='actor-one', project_id='ip-one',
                     idempotency_key='same-call', request_hash=changed_hash)
    assert store.get(record['id'], actor_id='actor-one', project_id='ip-one')['subject']['body'] == '具体正文'


def test_concurrent_native_create_has_one_durable_approval_record(tmp_path):
    store_for(tmp_path)
    barrier = threading.Barrier(8, timeout=10)
    request_hash = hashlib.sha256(b'same-input').hexdigest()
    def create(_):
        barrier.wait()
        return store_for(tmp_path).create(subject(), actor_id='actor-one', project_id='ip-one',
            idempotency_key='same-call', request_hash=request_hash)
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(create, range(8)))
    assert len({record['id'] for record in records}) == 1
    assert all(record['operationId'] is None and record['state'] == 'pending' for record in records)


def test_two_actors_concurrently_claiming_same_profile_have_one_winner(tmp_path):
    module = api()
    store = store_for(tmp_path)
    one = approved(store)
    two = approved(store, subject(body='other'), actor='actor-two')
    barrier = threading.Barrier(2, timeout=10)
    def claim(item):
        record, actor, value = item
        barrier.wait()
        try:
            return store_for(tmp_path).claim(record['id'], value, actor_id=actor, project_id='ip-one')
        except module.PublicationApprovalError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [(one, 'actor-one', subject()), (two, 'actor-two', subject(body='other'))]))
    assert sum(isinstance(value, dict) and value['claimed'] for value in results) == 1
    assert results.count(409) == 1


def test_finish_rejects_receipt_bound_to_another_payload(tmp_path):
    module = api()
    store = store_for(tmp_path)
    record = approved(store)
    claimed = store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')
    with pytest.raises(module.PublicationApprovalError) as caught:
        store.finish(record['id'], claimed['operationId'], {'status': 'confirmed', 'receiptId': 'remote', 'payloadHash': '0' * 64},
                     actor_id='actor-one', project_id='ip-one')
    assert caught.value.status_code == 409
    assert store.get(record['id'], actor_id='actor-one', project_id='ip-one')['result']['status'] == 'executing'


def test_profile_busy_is_internal_boolean_scoped_to_project_and_platform(tmp_path):
    store = store_for(tmp_path)
    assert store.profile_busy('douyin', project_id='ip-one') is False
    record = approved(store)
    assert store.profile_busy('douyin', project_id='ip-one') is False
    claimed = store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')
    assert store_for(tmp_path).profile_busy('douyin', project_id='ip-one') is True
    assert store.profile_busy('zhihu', project_id='ip-one') is False
    assert store.profile_busy('douyin', project_id='other-ip') is False
    store.finish(record['id'], claimed['operationId'], {'status': 'failed'}, actor_id='actor-one', project_id='ip-one')
    assert store.profile_busy('douyin', project_id='ip-one') is False


@pytest.mark.parametrize('reported', [[], {'code': 0}, 0, None])
def test_unrecognized_result_status_is_unknown_not_a_confirmation_or_crash(tmp_path, reported):
    store = store_for(tmp_path)
    record = approved(store)
    claimed = store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')
    settled = store.finish(record['id'], claimed['operationId'], {'status': reported, 'exitCode': 0},
                           actor_id='actor-one', project_id='ip-one')
    assert settled['result']['status'] == 'unknown'


def test_started_operation_can_record_result_after_approval_admission_deadline(tmp_path, monkeypatch):
    module = api()
    clock = [1_700_000_000_000]
    monkeypatch.setattr(module, '_now_ms', lambda: clock[0])
    store = store_for(tmp_path)
    record = store.create(subject(), actor_id='actor-one', project_id='ip-one', ttl_seconds=1)
    store.decide(record['id'], True, record['payloadHash'], actor_id='actor-one', project_id='ip-one')
    claimed = store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')
    clock[0] += 60_000
    assert store.get(record['id'], actor_id='actor-one', project_id='ip-one')['state'] == 'approved'
    settled = store.finish(record['id'], claimed['operationId'], {'status': 'confirmed', 'externalId': 'remote'},
                           actor_id='actor-one', project_id='ip-one')
    assert settled['result']['status'] == 'confirmed'
    assert store.claim(record['id'], subject(), actor_id='actor-one', project_id='ip-one')['claimed'] is False
