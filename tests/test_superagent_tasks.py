"""Durable Super Agent task decisions, scope, and lease fencing."""
from concurrent.futures import ThreadPoolExecutor

import pytest

SCOPE = {'actor_id': 'owner', 'project_id': 'default'}


@pytest.fixture
def task_store(tmp_path):
    from easel.superagent_tasks import SuperAgentTaskStore
    now = [1000]
    return SuperAgentTaskStore(tmp_path, now_ms=lambda: now[0]), now


def create(store, steps=None, request_id=None):
    return store.create(title='Draft task', brief='Make a sample', steps=steps or [
        {'kind': 'preference', 'payload': {}, 'retrySafe': True},
        {'kind': 'draft', 'payload': {}, 'retrySafe': True},
    ], request_id=request_id, **SCOPE)


def test_scoped_creation_replays_exact_and_rejects_conflicts(task_store):
    from easel.superagent_tasks import SuperAgentTaskError

    store, _ = task_store
    task = create(store, request_id='client-request')
    assert task == create(store, request_id='client-request')
    assert store.list(**SCOPE) == [task]
    assert store.list(actor_id='other', project_id='default') == []
    assert store.get(task['id'], actor_id='owner', project_id='other') is None
    with pytest.raises(SuperAgentTaskError) as error:
        create(store, steps=[{'kind': 'different', 'payload': {}}], request_id='client-request')
    assert error.value.status_code == 409


def test_expired_safe_lease_fences_stale_worker_and_preserves_checkpoints(task_store):
    from easel.superagent_tasks import SuperAgentTaskError, SuperAgentTaskStore

    store, now = task_store
    task = create(store)
    first = store.claim(task['id'], 'worker-a', lease_ms=50, **SCOPE)
    assert store.claim(task['id'], 'worker-b', **SCOPE) is None
    now[0] += 51
    reopened = SuperAgentTaskStore(store.path.parent, now_ms=lambda: now[0])
    second = reopened.claim(task['id'], 'worker-b', **SCOPE)
    assert second['step']['id'] == first['step']['id'] and second['leaseToken'] != first['leaseToken']
    assert second['step']['attempts'] == 2
    with pytest.raises(SuperAgentTaskError):
        store.finish_step(task['id'], first['leaseToken'], {'wrong': True}, **SCOPE)
    updated = reopened.finish_step(task['id'], second['leaseToken'], {'selected': 'default'}, **SCOPE)
    assert updated['status'] == 'queued'
    next_step = reopened.claim(task['id'], 'worker-b', **SCOPE)
    assert next_step['step']['kind'] == 'draft'
    assert next_step['task']['steps'][0]['receipt'] == {'selected': 'default'}


def test_expired_unsafe_step_freezes_uncertain_effect(task_store):
    from easel.superagent_tasks import SuperAgentTaskError

    store, now = task_store
    task = create(store, [{'kind': 'external', 'payload': {}, 'retrySafe': False}])
    assert store.claim(task['id'], 'worker-a', lease_ms=50, **SCOPE)
    now[0] += 51
    assert store.claim(task['id'], 'worker-b', **SCOPE) is None
    assert store.get(task['id'], **SCOPE)['status'] == 'unconfirmed'
    with pytest.raises(SuperAgentTaskError):
        store.retry(task['id'], **SCOPE)


def test_question_deadline_is_absolute_and_racing_answer_cannot_override_default(task_store):
    from easel.superagent_tasks import SuperAgentTaskStore

    store, now = task_store
    task = create(store)
    first = store.claim(task['id'], 'worker-a', **SCOPE)
    asked = store.ask(task['id'], first['leaseToken'], {'prompt': 'Style?', 'options': ['brief', 'detailed']},
                      default_answer='brief', deadline_ms=1100, **SCOPE)
    assert asked['status'] == 'waiting_input' and asked['question']['deadlineAt'] == 1100
    now[0] = 1100
    reopened = SuperAgentTaskStore(store.path.parent, now_ms=lambda: now[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(reopened.settle_due, task['id'], **SCOPE),
                 pool.submit(store.answer, task['id'], asked['question']['id'], 'detailed', **SCOPE)]
        [call.result(timeout=2) for call in calls]
    claimed = reopened.claim(task['id'], 'worker-b', **SCOPE)
    assert claimed['step']['question']['answer'] == 'brief'
    assert claimed['task']['question']['resolvedBy'] == 'default'


def test_approval_has_no_default_and_active_stop_requires_cleanup(task_store):
    from easel.superagent_tasks import SuperAgentTaskError

    store, now = task_store
    task = create(store)
    first = store.claim(task['id'], 'worker-a', **SCOPE)
    with pytest.raises(SuperAgentTaskError):
        store.ask(task['id'], first['leaseToken'], {'prompt': 'Publish?', 'options': ['yes', 'no'], 'requiresHuman': True},
                  default_answer='yes', deadline_ms=1100, **SCOPE)
    asked = store.ask(task['id'], first['leaseToken'], {'prompt': 'Publish?', 'options': ['yes', 'no'], 'requiresHuman': True},
                      deadline_ms=1100, **SCOPE)
    now[0] = 1100
    expired = store.settle_due(task['id'], **SCOPE)
    assert expired['status'] == 'failed' and expired['question']['status'] == 'expired'
    assert 'answer' not in expired['question']
    store.retry(task['id'], **SCOPE)
    worker = store.claim(task['id'], 'worker-b', **SCOPE)
    assert store.stop(task['id'], **SCOPE)['status'] == 'stopping'
    assert store.claim(task['id'], 'worker-c', **SCOPE) is None
    assert store.ack_stop(task['id'], worker['leaseToken'], **SCOPE)['status'] == 'stopped'


def test_parallel_claim_is_one_writer_and_keeps_checkpoint_on_recovery(task_store):
    store, now = task_store
    task = create(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.claim, task['id'], worker, lease_ms=50, **SCOPE)
                   for worker in ('worker-a', 'worker-b')]
        [claim] = [result for future in futures if (result := future.result(timeout=2)) is not None]
    token = claim['leaseToken']
    assert '_lease' not in claim['task'] and token not in str(store.get(task['id'], **SCOPE))
    store.checkpoint(task['id'], token, {'known': 'progress'}, **SCOPE)
    now[0] += 51
    resumed = store.claim(task['id'], 'worker-c', lease_ms=50, **SCOPE)
    assert resumed['step']['checkpoint'] == {'known': 'progress'}
    now[0] += 51
    assert store.claim(task['id'], 'worker-d', lease_ms=50, **SCOPE)
    now[0] += 51
    assert store.claim(task['id'], 'worker-e', **SCOPE) is None
    assert store.get(task['id'], **SCOPE)['status'] == 'failed'


def test_publish_and_risky_questions_cannot_opt_into_automatic_decisions(task_store):
    from easel.superagent_tasks import SuperAgentTaskError

    store, _ = task_store
    with pytest.raises(SuperAgentTaskError):
        create(store, [{'kind': 'publish', 'payload': {}, 'retrySafe': True}])
    task = create(store)
    claim = store.claim(task['id'], 'worker-a', **SCOPE)
    with pytest.raises(SuperAgentTaskError):
        store.ask(task['id'], claim['leaseToken'], {'prompt': 'Publish now?', 'options': ['yes', 'no']},
                  default_answer='yes', deadline_ms=1100, **SCOPE)
    with pytest.raises(SuperAgentTaskError):
        store.ask(task['id'], claim['leaseToken'], {'prompt': 'Please provide the password'},
                  deadline_ms=1100, **SCOPE)


def test_uncertain_publish_reconciles_only_durable_exact_confirmed_receipt(task_store):
    from easel.superagent_tasks import SuperAgentTaskError

    store, _ = task_store
    approval, operation, digest = 'a' * 32, 'b' * 32, 'c' * 64
    task = create(store, [{'kind': 'publish', 'payload': {'approvalId': approval}, 'retrySafe': False}])
    claim = store.claim(task['id'], 'worker', **SCOPE)
    store.checkpoint(task['id'], claim['leaseToken'], {'operationId': operation}, **SCOPE)
    store.fail_step(task['id'], claim['leaseToken'], 'submission uncertain', **SCOPE)
    receipt = {'id': approval, 'actorId': 'owner', 'projectId': 'default', 'operationId': operation,
               'payloadHash': digest, 'subject': {'platform': {'id': 'xhs'}, 'account': {'id': 'account'}, 'mode': 'publish_now'},
               'result': {'status': 'confirmed', 'phase': 'readback', 'evidenceMethod': 'authenticated_readback',
                          'publicationState': 'public', 'externalId': 'posted', 'operationId': operation,
                          'payloadHash': digest, 'platform': 'xhs', 'accountId': 'account', 'mode': 'publish_now'}}
    step_id = claim['step']['id']
    with pytest.raises(SuperAgentTaskError):
        store.reconcile_step(task['id'], step_id, {**receipt, 'actorId': 'different'}, **SCOPE)
    with pytest.raises(SuperAgentTaskError):
        store.reconcile_step(task['id'], step_id, {**receipt, 'result': {**receipt['result'], 'status': 'submitted'}}, **SCOPE)
    assert store.get(task['id'], **SCOPE)['status'] == 'unconfirmed'
    updated = store.reconcile_step(task['id'], step_id, receipt, **SCOPE)
    assert updated['status'] == 'completed' and updated['steps'][0]['receipt']['externalId'] == 'posted'
    assert store.reconcile_step(task['id'], step_id, receipt, **SCOPE) == updated


def test_scheduler_candidates_discover_actual_due_scope_not_future_wait(task_store):
    store, now = task_store
    task = create(store)
    claim = store.claim(task['id'], 'worker', **SCOPE)
    store.ask(task['id'], claim['leaseToken'], {'prompt': 'Style?', 'options': ['brief']},
              default_answer='brief', deadline_ms=1100, **SCOPE)
    other = store.create(title='Other scope', steps=[{'kind': 'draft'}], actor_id='different', project_id='other')
    assert store.scheduler_candidates() == [{'id': other['id'], 'actorId': 'different', 'projectId': 'other', 'status': 'queued'}]
    now[0] = 1100
    assert {row['id'] for row in store.scheduler_candidates()} == {task['id'], other['id']}


def test_explicit_retry_of_returned_review_requires_fresh_human_decision(task_store):
    store, _ = task_store
    task = create(store, [{'kind': 'review', 'retrySafe': True}])
    first = store.claim(task['id'], 'worker', **SCOPE)
    asked = store.ask(task['id'], first['leaseToken'], {'prompt': '核对草稿', 'options': ['通过', '退回'],
                      'requiresHuman': True}, deadline_ms=1100, **SCOPE)
    store.answer(task['id'], asked['question']['id'], '退回', **SCOPE)
    answered = store.claim(task['id'], 'worker', **SCOPE)
    store.fail_step(task['id'], answered['leaseToken'], {'code': 'review_returned'}, **SCOPE)
    store.retry(task['id'], **SCOPE)
    next_attempt = store.claim(task['id'], 'worker', **SCOPE)
    assert 'question' not in next_attempt['step']
    assert next_attempt['step']['previousQuestions'][0]['answer'] == '退回'
