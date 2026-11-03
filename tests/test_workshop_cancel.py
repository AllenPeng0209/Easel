"""B21 exact scoped cancellation and provider-ambiguity reconciliation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tests.test_chat_artifacts import artifact_http_environment
from tests.test_video_workshop import workshop_client
from tests.test_workshop_jobs import seed

RENDER = '2' * 32
TASK = 'b' * 32


def cancel_path(project):
    return f'/api/workshop/projects/{project["id"]}/renders/{RENDER}/cancel'


def test_cancel_rejects_wrong_task_then_confirms_exact_once(tmp_path):
    _, project, _ = seed(tmp_path)
    posts = []
    def cancel(_, receipt, *, before_post):
        before_post()
        posts.append(receipt['task_id'])
        return {'task_id': TASK, 'status': 'cancelled'}
    client = workshop_client(tmp_path, render_cancel=cancel, render_cancel_status=lambda *_: pytest.fail('No lookup expected'))
    path = cancel_path(project)
    assert client.post(path, json={'taskId': 'a' * 32}).status_code == 409
    assert posts == []
    first = client.post(path, json={'taskId': TASK})
    assert first.status_code == 200, first.text
    expected = first.json()
    assert expected['status'] == 'cancelled' and expected['jobStatus'] == 'cancelled'
    assert expected['taskId'] == TASK and expected['renderId'] == RENDER
    assert client.post(path, json={'taskId': TASK}).json() == expected
    assert posts == [TASK]
    jobs = {job['id']: job for job in client.get('/api/workshop/jobs').json()['jobs']}
    assert jobs[RENDER]['cancellation'] == expected and jobs[RENDER]['status'] == 'cancelled'
    assert jobs[RENDER]['canCancel'] is False and jobs[RENDER]['cancelReason'] == 'terminal'
    assert jobs['1' * 32]['status'] == 'awaiting_review' and 'cancellation' not in jobs['1' * 32]


def test_ambiguous_cancel_remains_durable_and_retry_only_observes_same_task(tmp_path):
    _, project, _ = seed(tmp_path)
    posts, reads = [], []
    def cancel(_, receipt, *, before_post):
        before_post()
        posts.append(receipt['task_id'])
        raise TimeoutError('private provider payload')
    def observe(_, receipt):
        reads.append(receipt['task_id'])
        return {'task_id': TASK, 'status': 'succeeded'}
    path = cancel_path(project)
    client = workshop_client(tmp_path, render_cancel=cancel, render_cancel_status=observe)
    first = client.post(path, json={'taskId': TASK})
    assert first.status_code == 200 and 'private' not in first.text
    assert first.json()['status'] == 'unconfirmed' and first.json()['jobStatus'] == 'running'
    assert reads == []
    reopened = workshop_client(tmp_path, render_cancel=cancel, render_cancel_status=observe)
    receipt = reopened.post(path, json={'taskId': TASK}).json()
    assert receipt['status'] == 'already_finished' and receipt['jobStatus'] == 'succeeded'
    assert posts == [TASK] and reads == [TASK]


def test_restarted_pending_intent_uses_exact_observation_and_rejects_foreign_task(tmp_path):
    store, project, _ = seed(tmp_path)
    render = project['renders'][1]
    render['cancellation'] = {'status': 'pending', 'taskId': TASK, 'renderId': RENDER,
                              'jobStatus': 'running', 'requestedAt': 1000, 'updatedAt': 1000}
    store._write(project)
    results = iter([{'task_id': 'f' * 32, 'status': 'cancelled'}, {'task_id': TASK, 'status': 'cancelled'}])
    client = workshop_client(tmp_path, render_cancel=lambda *_: pytest.fail('Restart cannot resend cancellation'),
                             render_status=lambda *_: pytest.fail('Cancellation requires strict readback'),
                             render_cancel_status=lambda *_: next(results))
    path = cancel_path(project)
    assert client.post(path, json={'taskId': TASK}).json()['status'] == 'unconfirmed'
    observed = client.get(path.removesuffix('/cancel')).json()
    assert observed['status'] == 'cancelled' and observed['cancellation']['requestedAt'] == 1000


def test_concurrent_duplicate_cannot_start_another_cancel(tmp_path):
    _, project, _ = seed(tmp_path)
    entered, finish = Event(), Event()
    posts = []
    def cancel(_, receipt, *, before_post):
        before_post()
        posts.append(receipt['task_id'])
        entered.set()
        assert finish.wait(3)
        return {'task_id': TASK, 'status': 'cancelled'}
    client = workshop_client(tmp_path, render_cancel=cancel, render_cancel_status=lambda *_: pytest.fail('In flight'))
    path = cancel_path(project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, path, json={'taskId': TASK})
        try:
            assert entered.wait(2)
            duplicate = pool.submit(client.post, path, json={'taskId': TASK}).result(timeout=2)
            assert duplicate.json()['status'] == 'pending'
            assert duplicate.json()['requestedAt'] is not None
        finally:
            finish.set()
        assert first.result(timeout=2).json()['status'] == 'cancelled'
    assert posts == [TASK]


@pytest.mark.access_integration
def test_cancel_route_requires_operation_and_keeps_ip_scope(artifact_http_environment):
    root, owner, members, anonymous, _ = artifact_http_environment
    _, project, _ = seed(root / 'outputs')
    path = cancel_path(project)
    assert anonymous().post(path, json={'taskId': TASK}).status_code == 401
    assert members['viewer'].post(path, json={'taskId': TASK}).status_code == 403
    assert members['operator'].post(path, json={'taskId': 'different-task'}).status_code == 409
    ip = owner.post('/api/projects', json={'name': 'Other IP jobs'}).json()['id']
    assert owner.post(f'/api/projects/{ip}' + path.removeprefix('/api'), json={'taskId': TASK}).status_code == 404


@pytest.mark.access_integration
def test_cancel_rechecks_original_authentication_after_provider_preflight(artifact_http_environment, monkeypatch):
    from easel import beeftv_bridge

    root, _, members, _, _ = artifact_http_environment
    operator = members['operator']
    _, project, _ = seed(root / 'outputs')
    calls = []

    class Provider:
        def request(self, method, path, **kwargs):
            calls.append((method, path))
            assert method == 'GET', 'A revoked browser session must not dispatch a mutation'
            assert operator.post('/api/auth/logout').status_code == 200
            return {'id': TASK, 'type': 'timeline_render', 'projectId': project['beeftv_canvas_id'],
                    'clientOperationId': 'assistant-render:' + project['renders'][1]['operation_id'], 'status': 'running'}

    monkeypatch.setattr(beeftv_bridge, 'configured_client', Provider)
    response = operator.post(cancel_path(project), json={'taskId': TASK})
    assert response.status_code == 403, response.text
    assert calls == [('GET', f'/tasks/{TASK}')]
