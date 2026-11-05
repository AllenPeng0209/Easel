"""A readback browser owns a durable platform lease without obtaining publication authority."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from easel import publication_approvals as approvals


SCOPE = {'actor_id': 'actor-one', 'project_id': 'ip-one'}


def subject(*, project='ip-one', platform='douyin', body='Exact approved content'):
    return {'project': {'id': project}, 'platform': {'id': platform}, 'account': {'id': 'uid:one'},
            'mode': 'publish_now', 'body': body, 'media': []}


def pending(store, *, actor='actor-one', project='ip-one', platform='douyin', body='Exact approved content'):
    scope = {'actor_id': actor, 'project_id': project}
    record = store.create(subject(project=project, platform=platform, body=body), **scope)
    return store.decide(record['id'], True, record['payloadHash'], **scope)


def ended(store, *, status='unknown', actor='actor-one', **changes):
    scope = {**SCOPE, 'actor_id': actor, 'project_id': changes.get('project', 'ip-one')}
    record = pending(store, actor=actor, **changes)
    started = store.claim(record['id'], record['subject'], **scope)
    return store.finish(record['id'], started['operationId'],
                        {'status': status, **({'externalId': 'post-one'} if status == 'confirmed' else {})}, **scope)


def lease_store(path):
    store = approvals.PublicationApprovalStore(path)
    assert callable(getattr(store, 'reserve_refresh', None)), 'Missing durable readback reservation'
    assert callable(getattr(store, 'release_refresh', None)), 'Missing exact readback cleanup barrier'
    assert callable(getattr(store, 'refresh_owned', None)), 'Missing same-operation refresh ownership check'
    return store


def reserve(store, record, **kwargs):
    return store.reserve_refresh(record['id'], record['operationId'], record['payloadHash'],
                                 actor_id=record['actorId'], project_id=record['projectId'], **kwargs)


def release(store, record, token, *, cleanup_confirmed=True, **changes):
    reference = {'identifier': record['id'], 'operation_id': record['operationId'], 'payload_hash': record['payloadHash'],
                 'actor_id': record['actorId'], 'project_id': record['projectId'], **changes}
    return store.release_refresh(token=token, cleanup_confirmed=cleanup_confirmed, **reference)


def owned(store, record, token, **changes):
    reference = {'identifier': record['id'], 'operation_id': record['operationId'], 'payload_hash': record['payloadHash'],
                 'actor_id': record['actorId'], 'project_id': record['projectId'], **changes}
    return store.refresh_owned(token=token, **reference)


def test_existing_two_table_database_migrates_without_rewriting_any_approval(tmp_path):
    store = approvals.PublicationApprovalStore(tmp_path)
    record = ended(store)
    with sqlite3.connect(store.path) as database:
        database.execute('DROP TABLE IF EXISTS publication_profile_refresh')
    restored = approvals.PublicationApprovalStore(tmp_path)
    with sqlite3.connect(restored.path) as database:
        tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'publication_profile_refresh' in tables, 'Legacy state must gain the new lease table'
    assert restored.get(record['id'], **SCOPE) == record


def test_reserve_and_release_never_create_an_operation_or_rewrite_the_existing_result(tmp_path):
    store = lease_store(tmp_path)
    record = ended(store)
    lease = reserve(store, record)
    assert lease['reserved'] is True and len(lease['token']) == 32
    assert owned(store, record, lease['token']) is True
    assert store.get(record['id'], **SCOPE) == record
    assert release(store, record, lease['token']) is True
    assert owned(store, record, lease['token']) is False
    assert store.get(record['id'], **SCOPE) == record
    with sqlite3.connect(store.path) as database:
        assert database.execute('SELECT COUNT(*) FROM publication_approvals').fetchone()[0] == 1


def test_concurrent_actors_reserve_one_same_profile_lease_across_independent_store_instances(tmp_path):
    store = lease_store(tmp_path)
    records = [ended(store, actor=f'actor-{index}', body=f'content-{index}') for index in range(8)]
    barrier = threading.Barrier(len(records), timeout=5)

    def run(record):
        candidate = approvals.PublicationApprovalStore(tmp_path)
        barrier.wait()
        try:
            return reserve(candidate, record, min_interval_ms=0)
        except approvals.PublicationApprovalError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=len(records)) as pool:
        results = list(pool.map(run, records))
    assert sum(isinstance(item, dict) and item.get('reserved') is True for item in results) == 1
    assert results.count(409) == len(records) - 1


def test_active_refresh_blocks_another_actor_claim_and_account_mutation_hint_until_exact_cleanup(tmp_path):
    store = lease_store(tmp_path)
    previous = ended(store)
    new = pending(store, actor='actor-two', body='another approved publication')
    lease = reserve(store, previous)
    assert store.profile_busy('douyin', project_id='ip-one') is True
    assert store.profile_busy('zhihu', project_id='ip-one') is False
    assert store.profile_busy('douyin', project_id='another-ip') is False
    with pytest.raises(approvals.PublicationApprovalError) as blocked:
        store.claim(new['id'], new['subject'], actor_id='actor-two', project_id='ip-one')
    assert blocked.value.status_code == 409
    assert store.get(new['id'], actor_id='actor-two', project_id='ip-one')['operationId'] is None
    assert release(store, previous, lease['token']) is True
    assert store.profile_busy('douyin', project_id='ip-one') is False
    assert store.claim(new['id'], new['subject'], actor_id='actor-two', project_id='ip-one')['claimed'] is True


@pytest.mark.parametrize('executing_same_record', [False, True])
def test_existing_active_execution_never_admits_a_refresh(tmp_path, executing_same_record):
    store = lease_store(tmp_path)
    if executing_same_record:
        active = pending(store)
        target = store.claim(active['id'], active['subject'], **SCOPE)
    else:
        target = ended(store)
        active = pending(store, actor='actor-two', body='active second publication')
        store.claim(active['id'], active['subject'], actor_id='actor-two', project_id='ip-one')
    before = store.get(target['id'], **SCOPE)
    with pytest.raises(approvals.PublicationApprovalError) as blocked:
        reserve(store, target)
    assert blocked.value.status_code == 409
    assert store.get(target['id'], **SCOPE) == before
    assert store.profile_busy('douyin', project_id='ip-one') is True


@pytest.mark.parametrize('state', ['pending', 'confirmed'])
def test_unexecuted_or_already_confirmed_record_cannot_reserve_a_browser(tmp_path, state):
    store = lease_store(tmp_path)
    record = ended(store, status='confirmed') if state == 'confirmed' else pending(store)
    with pytest.raises(approvals.PublicationApprovalError) as blocked:
        reserve(store, record)
    assert blocked.value.status_code == 409
    assert store.get(record['id'], **SCOPE) == record


@pytest.mark.parametrize('field', ['operation_id', 'payload_hash'])
def test_reservation_rejects_a_rebound_operation_or_hash_without_mutating_authority(tmp_path, field):
    store = lease_store(tmp_path)
    record = ended(store)
    arguments = {'identifier': record['id'], 'operation_id': record['operationId'], 'payload_hash': record['payloadHash'],
                 **SCOPE, field: 'f' * (64 if field == 'payload_hash' else 32)}
    with pytest.raises(approvals.PublicationApprovalError) as blocked:
        store.reserve_refresh(**arguments)
    assert blocked.value.status_code == 409
    assert store.get(record['id'], **SCOPE) == record
    assert store.profile_busy('douyin', project_id='ip-one') is False


def test_released_refresh_is_rate_limited_per_platform_across_actors_and_clock_rollback(tmp_path, monkeypatch):
    clock = [1_700_000_000_000]
    monkeypatch.setattr(approvals, '_now_ms', lambda: clock[0])
    store = lease_store(tmp_path)
    first = ended(store)
    second = ended(store, actor='actor-two', body='another former operation')
    lease = reserve(store, first)
    assert release(store, first, lease['token']) is True
    for delta in [-1000, 0, 29999]:
        clock[0] = 1_700_000_000_000 + delta
        assert reserve(store, second) == {'reserved': False}
        assert store.profile_busy('douyin', project_id='ip-one') is False
        assert store.get(second['id'], actor_id='actor-two', project_id='ip-one') == second
    clock[0] = 1_700_000_030_000
    next_lease = reserve(store, second)
    assert next_lease['reserved'] is True and next_lease['token'] != lease['token']


@pytest.mark.parametrize('cleanup', [False, None, 0, 1, 'true'])
def test_unconfirmed_cleanup_keeps_durable_lock_without_any_ttl(tmp_path, monkeypatch, cleanup):
    clock = [1_700_000_000_000]
    monkeypatch.setattr(approvals, '_now_ms', lambda: clock[0])
    store = lease_store(tmp_path)
    record = ended(store)
    lease = reserve(store, record)
    assert release(store, record, lease['token'], cleanup_confirmed=cleanup) is False
    clock[0] += 100 * 365 * 24 * 60 * 60 * 1000
    restored = approvals.PublicationApprovalStore(tmp_path)
    assert owned(restored, record, lease['token']) is True
    assert restored.profile_busy('douyin', project_id='ip-one') is True
    with pytest.raises(approvals.PublicationApprovalError) as blocked:
        reserve(restored, record, min_interval_ms=0)
    assert blocked.value.status_code == 409
    assert release(restored, record, lease['token']) is True
    assert restored.profile_busy('douyin', project_id='ip-one') is False


@pytest.mark.parametrize('field', ['token', 'operation_id', 'payload_hash'])
def test_only_exact_reference_can_release_or_report_the_active_refresh(tmp_path, field):
    store = lease_store(tmp_path)
    record = ended(store)
    lease = reserve(store, record)
    bad = 'f' * (64 if field == 'payload_hash' else 32)
    if field == 'token':
        assert release(store, record, bad) is False
        assert owned(store, record, bad) is False
    else:
        assert release(store, record, lease['token'], **{field: bad}) is False
        assert owned(store, record, lease['token'], **{field: bad}) is False
    assert owned(store, record, lease['token']) is True
    assert store.profile_busy('douyin', project_id='ip-one') is True


@pytest.mark.parametrize('scope', [{'actor_id': 'another-actor', 'project_id': 'ip-one'},
                                 {'actor_id': 'actor-one', 'project_id': 'another-ip'}])
def test_another_actor_or_ip_cannot_reserve_release_or_read_a_lease(tmp_path, scope):
    store = lease_store(tmp_path)
    record = ended(store)
    lease = reserve(store, record)
    calls = [lambda: store.reserve_refresh(record['id'], record['operationId'], record['payloadHash'], **scope),
             lambda: release(store, record, lease['token'], **scope),
             lambda: owned(store, record, lease['token'], **scope)]
    for call in calls:
        with pytest.raises(approvals.PublicationApprovalError) as hidden:
            call()
        assert hidden.value.status_code == 404
    assert owned(store, record, lease['token']) is True


def test_refresh_and_new_publication_racing_for_one_profile_have_only_one_admission(tmp_path):
    store = lease_store(tmp_path)
    record = ended(store)
    new = pending(store, actor='actor-two', body='new publication')
    barrier = threading.Barrier(2, timeout=5)

    def run(which):
        candidate = approvals.PublicationApprovalStore(tmp_path)
        barrier.wait()
        try:
            if which == 'refresh':
                return reserve(candidate, record)
            return candidate.claim(new['id'], new['subject'], actor_id='actor-two', project_id='ip-one')
        except approvals.PublicationApprovalError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ['refresh', 'publish']))
    assert sum(isinstance(item, dict) and (item.get('reserved') or item.get('claimed')) for item in results) == 1
    assert results.count(409) == 1


@pytest.mark.parametrize('interval', [True, False, -1, .5, None, float('nan')])
def test_rate_limit_interval_is_an_explicit_nonnegative_integer(tmp_path, interval):
    store = lease_store(tmp_path)
    record = ended(store)
    with pytest.raises(approvals.PublicationApprovalError) as invalid:
        reserve(store, record, min_interval_ms=interval)
    assert invalid.value.status_code == 400
    assert store.profile_busy('douyin', project_id='ip-one') is False
