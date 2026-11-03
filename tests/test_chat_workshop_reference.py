"""D28 hands verified workshop context to a scoped chat turn."""
import hashlib
import json

import pytest

from easel.projects import project_context
from easel.request_identity import actor_context
from easel.video_workshop import WorkshopStore
from tests.test_chat_artifacts import artifact_http_environment
from tests.test_chat_topic_reference import install_runner
import web.app as web

pytestmark = pytest.mark.access_integration


@pytest.fixture
def workshop(artifact_http_environment, monkeypatch):
    root, owner, members, _, _ = artifact_http_environment
    outputs = root / 'outputs'
    store = WorkshopStore(outputs, strict_canvas_bindings=True)
    project = store.create({'title': 'Saved workshop', 'brief': 'Historical edit context'},
                           canvas_creator=lambda _: 'canvas-fixture')
    path = outputs / 'topic' / 'historical.mp4'
    path.write_bytes(b'older delivery')
    artifact_id = 'a' * 32
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    project.update(revision=4, deliverables=[
        {'id': artifact_id, 'revision': 2, 'engine_revision': 3, 'sha256': sha, 'path': 'topic/historical.mp4'},
        {'id': 'b' * 32, 'revision': 4, 'engine_revision': 6, 'sha256': 'a' * 64, 'path': 'topic/newer.mp4'},
    ])
    store._write(project)
    monkeypatch.setattr(web, '_beeftv_canvas_state', lambda _: {'revision': 7})
    return owner, members, path, store, project, {
        'id': project['id'], 'revision': 4, 'engineRevision': 7,
        'artifactId': artifact_id, 'artifactSha256': sha,
    }


def request_for(ref, turn='workshop-turn'):
    return {'message': 'Continue the edit', 'sessionId': 'workshop-chat', 'turnId': turn, 'workshopRef': ref}


@pytest.mark.parametrize('runtime', ['api', 'codex'])
def test_selected_historical_delivery_is_exact_in_prompt_stream_and_snapshot(workshop, monkeypatch, runtime):
    owner, _, _, _, project, reference = workshop
    prompts = []
    monkeypatch.setattr(web, '_chat_backend', lambda _: runtime)
    monkeypatch.setattr(web, '_chat_api_settings', lambda: {})
    monkeypatch.setattr(web, '_harness_settings', lambda: {'models': {}, 'sandbox': 'read-only'})

    async def direct(settings, content, **kwargs):
        prompts.append(content)
        yield 'token', 'done'
        yield 'finish', 'stop'

    class Adapter:
        async def run(self, request, emit, ask, bind):
            prompts.append(request.prompt)
            bind('workshop-native-fixture')
            emit('token', 'done')

    monkeypatch.setattr(web, 'stream_chat', direct)
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    response = owner.post('/api/chat/stream', json=request_for(reference))
    assert response.status_code == 200, response.text
    assert len(prompts) == 1
    assert 'Historical edit context' in prompts[0]
    assert reference['artifactSha256'] in prompts[0]
    assert 'topic/historical.mp4' not in prompts[0]
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith('data: {') and '"kind": "business"' in line]
    assert len(events) == 1
    assert events[0]['callId'] == 'host:workshop:workshop-turn'
    card = events[0]['business']
    assert card['id'] == project['id']
    assert card['details']['handoff'] == {
        'revision': 4, 'engineRevision': 7, 'artifactId': reference['artifactId'],
        'artifactSha256': reference['artifactSha256'], 'artifactRevision': 2, 'artifactEngineRevision': 3,
    }
    assert card['details']['publishEligibility'] == 'unverified'
    replay = owner.get('/api/chat/jobs/workshop-turn/stream')
    assert events[0]['callId'] in replay.text
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', web.PROJECT_ROOT):
        web._job_event_file('workshop-turn').unlink()
    assert owner.get('/api/chat/last/workshop-chat', params={'turn_id': 'workshop-turn'}).json()['events'] == events


@pytest.mark.parametrize('changes', [{'revision': 3}, {'engineRevision': 6}, {'artifactSha256': 'f' * 64}])
def test_stale_context_refuses_new_turn_without_claim(workshop, monkeypatch, changes):
    owner, _, _, _, _, reference = workshop
    prompts = install_runner(monkeypatch, 'api')
    assert owner.post('/api/chat/stream', json=request_for({**reference, **changes})).status_code == 409
    assert not prompts
    assert owner.post('/api/chat/stream', json=request_for(reference)).status_code == 200
    assert len(prompts) == 1


def test_admitted_workshop_turn_replays_after_selected_bytes_change(workshop, monkeypatch):
    owner, _, path, _, _, reference = workshop
    prompts = install_runner(monkeypatch, 'api')
    request = request_for(reference)
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    path.write_bytes(b'mutated')
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert owner.post('/api/chat/stream', json=request_for(reference, 'new-turn')).status_code == 409
    assert len(prompts) == 1


def test_workshop_reference_cannot_cross_ip_or_bypass_viewer_scope(workshop, monkeypatch):
    owner, members, _, _, _, reference = workshop
    prompts = install_runner(monkeypatch, 'api')
    other = owner.post('/api/projects', json={'name': 'Other workshop IP'}).json()['id']
    assert owner.post(f'/api/projects/{other}/chat/stream', json=request_for(reference)).status_code == 404
    assert members['viewer'].post('/api/chat/stream', json=request_for(reference)).status_code == 403
    assert not prompts


def test_engine_unavailable_and_unbound_canvas_fail_closed(workshop, monkeypatch):
    owner, _, _, store, project, reference = workshop
    prompts = install_runner(monkeypatch, 'api')
    monkeypatch.setattr(web, '_beeftv_canvas_state', lambda _: None)
    assert owner.post('/api/chat/stream', json=request_for(reference)).status_code == 503
    store._write({**project, 'beeftv_canvas_id': 'unbound-canvas'})
    assert owner.post('/api/chat/stream', json=request_for(reference)).status_code == 403
    assert not prompts


def test_reference_schema_requires_paired_artifact_and_no_client_context(workshop, monkeypatch):
    owner, _, _, _, _, reference = workshop
    prompts = install_runner(monkeypatch, 'api')
    incomplete = {key: value for key, value in reference.items() if key != 'artifactSha256'}
    for invalid in (incomplete, {**reference, 'path': 'outputs/topic/historical.mp4'}, {**reference, 'revision': True}):
        assert owner.post('/api/chat/stream', json=request_for(invalid)).status_code == 422
    assert not prompts


def test_project_revision_is_rechecked_after_live_canvas_read(workshop, monkeypatch):
    owner, _, _, store, project, reference = workshop
    prompts = install_runner(monkeypatch, 'api')

    def changed(_):
        store._write({**project, 'revision': project['revision'] + 1})
        return {'revision': reference['engineRevision']}

    monkeypatch.setattr(web, '_beeftv_canvas_state', changed)
    assert owner.post('/api/chat/stream', json=request_for(reference)).status_code == 409
    assert not prompts


def test_selected_delivery_cannot_follow_symlink_even_with_matching_bytes(workshop, monkeypatch):
    owner, _, path, _, _, reference = workshop
    prompts = install_runner(monkeypatch, 'api')
    outside = web.PROJECT_ROOT / 'outside-workshop.mp4'
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert owner.post('/api/chat/stream', json=request_for(reference)).status_code == 409
    assert not prompts


def test_workshop_with_no_engine_or_selection_reads_local_context_only(workshop, monkeypatch):
    owner, _, _, store, project, reference = workshop
    prompts = install_runner(monkeypatch, 'api')

    def unexpected(_):
        pytest.fail('No live canvas read was requested')

    monkeypatch.setattr(web, '_beeftv_canvas_state', unexpected)
    before = store._path(project['id']).read_bytes()
    request = request_for({'id': reference['id'], 'revision': reference['revision']})
    response = owner.post('/api/chat/stream', json=request)
    assert response.status_code == 200, response.text
    assert store._path(project['id']).read_bytes() == before
    assert '"engineRevision": null' in prompts[0] and '"artifactId": null' in prompts[0]


def test_topic_and_workshop_can_coexist_in_same_turn(workshop, monkeypatch):
    owner, _, _, _, _, reference = workshop
    prompts = install_runner(monkeypatch, 'api')
    monkeypatch.setattr(web, 'IDEAS_FILE', web.PROJECT_ROOT / 'outputs/_ideas.json')
    idea = owner.post('/api/ideas', json={'title': 'Existing topic'}).json()
    card = owner.get('/api/chat/business-objects', params={
        'action': 'inspect', 'kind': 'idea', 'id': idea['id'],
    }).json()['items'][0]
    response = owner.post('/api/chat/stream', json={**request_for(reference),
        'topicRef': {'id': idea['id'], 'snapshotHash': card['snapshotHash']}})
    assert response.status_code == 200, response.text
    assert 'host:topic:workshop-turn' in response.text and 'host:workshop:workshop-turn' in response.text
    assert '<topic_reference>' in prompts[0] and '<workshop_reference>' in prompts[0]


def test_corrupt_selected_delivery_cannot_embed_arbitrary_context(workshop, monkeypatch):
    owner, _, _, store, project, reference = workshop
    prompts = install_runner(monkeypatch, 'api')
    project['deliverables'][0]['engine_revision'] = {'private': 'fixture'}
    store._write(project)
    response = owner.post('/api/chat/stream', json=request_for(reference))
    assert response.status_code == 503
    assert 'fixture' not in response.text and not prompts
