"""B19 roster exposes durable render and adoption observations without runtime fanout."""
import pytest

from easel.projects import project_context
from tests.test_chat_artifacts import artifact_http_environment
from tests.test_video_workshop import workshop_client
from easel.video_workshop import WorkshopStore


def seed(root, title='Private project'):
    store = WorkshopStore(root, strict_canvas_bindings=True)
    project = store.create({'title': title, 'brief': 'do not project this brief', 'aspect_ratio': '9:16'},
                           canvas_creator=lambda value: 'canvas-' + value['id'])
    artifact = {'id': 'a' * 32, 'render_id': '1' * 32, 'path': 'private/location.mp4',
                'revision': 2, 'sha256': 'b' * 64, 'engine_revision': 4}
    project.update(revision=2, deliverables=[artifact], reviews=[
        {'kind': 'simulated', 'decision': 'approved', 'revision': 2,
         'artifact_id': artifact['id'], 'artifact_sha256': artifact['sha256']},
        {'kind': 'human', 'decision': 'approved', 'revision': 2,
         'artifact_id': artifact['id'], 'artifact_sha256': 'c' * 64},
    ])
    common = {'content_revision': 1, 'engine_revision': 4, 'submission_state': 'confirmed',
              'created_at': '2026-11-03T00:00:00+00:00', 'updated_at': '2026-11-03T00:00:30+00:00',
              'operation_id': 'private-operation-id', 'message': 'private diagnostic',
              'canvas_id': project['beeftv_canvas_id']}
    project['renders'] = [
        {**common, 'id': '1' * 32, 'task_id': 'task-adopted', 'status': 'succeeded',
         'artifact_id': artifact['id'], 'collected_revision': 2, 'completed_at': '2026-11-03T00:00:10+00:00'},
        {**common, 'id': '2' * 32, 'task_id': 'task-live', 'status': 'running', 'progress': .4, 'progress_known': True},
        {**common, 'id': '3' * 32, 'task_id': 'task-unconfirmed', 'status': 'failed', 'progress': 0,
         'submission_state': 'unconfirmed'},
        {**common, 'id': '4' * 32, 'task_id': 'task-rendered', 'status': 'succeeded', 'progress': 1},
    ]
    store._write(project)
    return store, project, artifact


def test_job_roster_preserves_exact_adoption_known_progress_and_no_runtime_fanout(tmp_path):
    store, project, artifact = seed(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError('Roster must not contact the runtime')

    client = workshop_client(tmp_path, render_status=forbidden, render_start=forbidden,
                             render_collect=forbidden, canvas_state=forbidden)
    before = store._path(project['id']).read_bytes()
    response = client.get('/api/workshop/jobs')
    assert response.status_code == 200, response.text
    assert response.json()['projectId'] == 'default'
    jobs = {job['id']: job for job in response.json()['jobs']}
    adopted, live, unknown, rendered = (jobs[str(index) * 32] for index in range(1, 5))
    assert (adopted['status'], adopted['artifactId'], adopted['artifactSha256']) == (
        'awaiting_review', artifact['id'], artifact['sha256'])
    assert adopted['finishedAt'] - adopted['createdAt'] == adopted['elapsedMs'] == 10_000
    assert adopted['progress'] is None
    assert live['progress'] == .4 and live['elapsedMs'] is None and live['status'] == 'running'
    assert unknown['status'] == 'unconfirmed' and unknown['taskId'] is None and unknown['progress'] is None
    assert rendered['status'] == 'awaiting_collection' and rendered['finishedAt'] is None
    assert 'private' not in response.text.lower().replace('private project', '')
    assert store._path(project['id']).read_bytes() == before
    project['reviews'].append({'kind': 'human', 'decision': 'approved', 'revision': 2,
                               'artifact_id': artifact['id'], 'artifact_sha256': artifact['sha256']})
    store._write(project)
    assert next(job for job in client.get('/api/workshop/jobs').json()['jobs']
                if job['id'] == adopted['id'])['status'] == 'completed'
    project['reviews'].append({'kind': 'human', 'decision': 'changes_requested', 'revision': 2,
                               'artifact_id': artifact['id'], 'artifact_sha256': artifact['sha256']})
    store._write(project)
    assert next(job for job in client.get('/api/workshop/jobs').json()['jobs']
                if job['id'] == adopted['id'])['status'] == 'changes_requested'


@pytest.mark.access_integration
def test_job_roster_scopes_membership_and_ip_without_mixing_receipts(artifact_http_environment):
    root, owner, members, anonymous, _ = artifact_http_environment
    store, project, _ = seed(root / 'outputs', title='Default job')
    project['renders'][0]['canvas_id'] = 'foreign-unbound-canvas'
    store._write(project)
    ip = owner.post('/api/projects', json={'name': 'Other IP jobs'}).json()['id']
    with project_context(ip, root):
        import web.app as web
        seed(web._outputs_dir(), title='Other job')
    default = '/api/workshop/jobs'
    other = f'/api/projects/{ip}/workshop/jobs'
    assert anonymous().get(default).status_code == 401
    viewer_jobs = members['viewer'].get(default).json()['jobs']
    assert {job['title'] for job in viewer_jobs} == {'Default job'}
    unbound = next(job for job in viewer_jobs if job['id'] == '1' * 32)
    assert unbound['status'] == 'unconfirmed' and unbound['taskId'] is None and unbound['artifactId'] is None
    assert members['operator'].get(other).status_code == 403
    scoped = owner.get(other)
    assert scoped.status_code == 200 and scoped.json()['projectId'] == ip
    assert {job['title'] for job in scoped.json()['jobs']} == {'Other job'}
