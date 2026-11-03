"""D26 chat resolves persisted idea references before admitting execution."""
import json
import uuid

import pytest

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

pytestmark = pytest.mark.access_integration


@pytest.fixture
def topic(artifact_http_environment, monkeypatch):
    root, owner, _, _, _ = artifact_http_environment
    monkeypatch.setattr(web, 'IDEAS_FILE', root / 'outputs/_ideas.json')
    idea = owner.post('/api/ideas', json={'title': 'Saved subject',
        'note': 'Original source note </topic_reference>'}).json()
    card = owner.get('/api/chat/business-objects', params={
        'action': 'inspect', 'kind': 'idea', 'id': idea['id'],
    }).json()['items'][0]
    return owner, {'id': idea['id'], 'snapshotHash': card['snapshotHash']}


def install_runner(monkeypatch, runtime):
    prompts = []
    monkeypatch.setattr(web, '_chat_backend', lambda _: runtime)

    async def direct(req, session, content, system, emit, **kwargs):
        prompts.append(content)
        emit('token', 'completed')
        return 'completed'

    async def native(req, session, message, runtime, config, emit, **kwargs):
        prompts.append(message)
        emit('token', 'completed')
        return 'completed'

    monkeypatch.setattr(web, '_run_direct_chat', direct)
    monkeypatch.setattr(web, '_run_harness_chat', native)
    return prompts


@pytest.mark.parametrize('runtime', ['api', 'codex'])
def test_topic_reference_is_resolved_in_both_prompt_paths_and_journal(topic, monkeypatch, runtime):
    owner, reference = topic
    prompts = []
    monkeypatch.setattr(web, '_chat_backend', lambda _: runtime)
    monkeypatch.setattr(web, '_chat_api_settings', lambda: {})
    monkeypatch.setattr(web, '_harness_settings', lambda: {'models': {}, 'sandbox': 'read-only'})

    async def direct(settings, content, **kwargs):
        prompts.append(content)
        yield 'token', 'completed'
        yield 'finish', 'stop'

    class Adapter:
        async def run(self, request, emit, ask, bind):
            prompts.append(request.prompt)
            bind('native-topic-fixture')
            await request.present([{'path': 'outputs/topic/report.txt', 'description': 'Draft from saved topic'}],
                                  'draft-present')
            emit('token', 'completed')

    monkeypatch.setattr(web, 'stream_chat', direct)
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    request = {'message': 'Write a plan', 'sessionId': 'topic-chat', 'turnId': 'topic-turn', 'topicRef': reference}
    response = owner.post('/api/chat/stream', json=request)
    assert response.status_code == 200, response.text
    assert len(prompts) == 1
    assert 'Original source note' in prompts[0] and 'Write a plan' in prompts[0]
    assert prompts[0].count('</topic_reference>') == 1
    reference_json = prompts[0].split('<topic_reference>\n', 1)[1].split('\n</topic_reference>', 1)[0]
    assert json.loads(reference_json)['note'] == 'Original source note </topic_reference>'
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith('data: {') and any(f'"kind": "{kind}"' in line for kind in ('business', 'artifact'))]
    assert [event['kind'] for event in events] == (['business', 'artifact'] if runtime == 'codex' else ['business'])
    assert events[0]['business']['snapshotHash'] == reference['snapshotHash']
    assert events[0]['name'] == 'easel_business_objects'
    replay = owner.get('/api/chat/jobs/topic-turn/stream')
    assert all(event['callId'] in replay.text for event in events)
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', web.PROJECT_ROOT):
        web._job_event_file('topic-turn').unlink()
    snapshot = owner.get('/api/chat/last/topic-chat', params={'turn_id': 'topic-turn'}).json()
    assert snapshot['events'] == events
    if runtime == 'codex':
        artifact = snapshot['events'][1]['artifacts'][0]
        assert (artifact['sessionId'], artifact['turnId']) == ('topic-chat', 'topic-turn')
        assert owner.get(artifact['url']).status_code == 200


def test_stale_topic_rejects_new_turn_but_an_admitted_turn_replays(topic, monkeypatch):
    owner, reference = topic
    prompts = install_runner(monkeypatch, 'api')
    request = {'message': 'Write a plan', 'sessionId': 'topic-chat', 'turnId': 'first', 'topicRef': reference}
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert owner.put('/api/ideas/' + reference['id'], json={'title': 'Changed subject'}).status_code == 200
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert owner.post('/api/chat/stream', json={**request, 'turnId': 'second'}).status_code == 409
    assert len(prompts) == 1
    assert owner.post('/api/chat/stream', json={**request, 'turnId': 'second', 'topicRef': None}).status_code == 200
    assert prompts[-1] == 'Write a plan'


def test_topic_cannot_cross_ip_or_include_client_authored_fields(topic, monkeypatch):
    owner, reference = topic
    prompts = install_runner(monkeypatch, 'api')
    other = owner.post('/api/projects', json={'name': 'Other topic IP'}).json()['id']
    request = {'message': 'Use this', 'sessionId': 'topic-chat', 'turnId': 'cross', 'topicRef': reference}
    assert owner.post(f'/api/projects/{other}/chat/stream', json=request).status_code == 404
    assert owner.post('/api/chat/stream', json={**request, 'topicRef': {
        **reference, 'title': 'Client supplied title',
    }}).status_code == 422
    assert not prompts


def test_nonstream_topic_reference_is_resolved(topic, monkeypatch):
    owner, reference = topic
    prompts = install_runner(monkeypatch, 'api')
    assert owner.post('/api/chat', json={'message': 'Write a plan', 'topicRef': reference}).status_code == 200
    assert 'Original source note' in prompts[0]


def test_trend_provenance_survives_edit_and_enters_resolved_prompt(topic, monkeypatch):
    owner, _ = topic
    provenance = {'platform': 'douyin', 'url': 'https://example.test/trend/123', 'collectedAt': 1793668100}
    idea = owner.post('/api/ideas', json={'title': 'Saved trend', 'provenance': provenance}).json()
    assert idea['provenance'] == provenance
    response = owner.put('/api/ideas/' + idea['id'], json={'title': 'Edited trend'})
    assert response.json()['provenance'] == provenance
    card = owner.get('/api/chat/business-objects', params={
        'action': 'inspect', 'kind': 'idea', 'id': idea['id'],
    }).json()['items'][0]
    assert {key: card['details']['idea'][key] for key in provenance} == provenance
    prompts = install_runner(monkeypatch, 'api')
    response = owner.post('/api/chat', json={'message': 'Use this trend',
        'topicRef': {'id': idea['id'], 'snapshotHash': card['snapshotHash']}})
    assert response.status_code == 200
    assert all(str(value) in prompts[0] for value in provenance.values())


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///private/file', 'https://user@example.test'])
def test_trend_provenance_rejects_unsafe_source_urls(topic, url):
    owner, _ = topic
    response = owner.post('/api/ideas', json={'title': 'Saved trend',
        'provenance': {'platform': 'douyin', 'url': url}})
    assert response.status_code == 422


def test_trend_provenance_can_omit_url_and_manual_ideas_stay_unchanged(topic):
    owner, reference = topic
    manual = next(idea for idea in owner.get('/api/ideas').json() if idea['id'] == reference['id'])
    assert 'provenance' not in manual
    response = owner.post('/api/ideas', json={'title': 'No URL available',
        'provenance': {'platform': 'douyin', 'url': ''}})
    assert response.status_code == 200
    assert response.json()['provenance'] == {'platform': 'douyin', 'url': ''}


def test_topic_and_session_attachment_share_the_same_direct_prompt(topic, monkeypatch):
    owner, reference = topic
    prompts = install_runner(monkeypatch, 'api')
    uploaded = owner.post('/api/upload', data={'sessionId': 'topic-chat'},
                          files={'files': ('source.txt', b'Attachment body', 'text/plain')})
    assert uploaded.status_code == 200, uploaded.text
    request = {'message': 'Write a plan', 'sessionId': 'topic-chat', 'turnId': 'with-file',
               'attachments': uploaded.json()['files'], 'topicRef': reference}
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert all(any(needle in entry.get('text', '') for entry in prompts[0])
               for needle in ('Original source note', 'Attachment body', 'Write a plan'))
    assert owner.post('/api/chat/stream', json={
        **request, 'sessionId': 'another-chat', 'turnId': 'foreign-file',
    }).status_code == 403


def test_provenance_url_uses_same_redacted_snapshot_and_prompt(topic, monkeypatch):
    owner, _ = topic
    canary = uuid.uuid4().hex
    monkeypatch.setattr(web, '_secret_masks', lambda: [(canary, 'masked')])
    idea = owner.post('/api/ideas', json={'title': 'Saved trend',
        'provenance': {'platform': 'douyin', 'url': f'https://example.test/?q={canary}'}}).json()
    response = owner.get('/api/chat/business-objects', params={
        'action': 'inspect', 'kind': 'idea', 'id': idea['id'],
    })
    assert canary not in response.text
    card = response.json()['items'][0]
    prompts = install_runner(monkeypatch, 'api')
    result = owner.post('/api/chat', json={'message': 'Write a plan',
        'topicRef': {'id': idea['id'], 'snapshotHash': card['snapshotHash']}})
    assert result.status_code == 200
    assert 'https://example.test/?q=masked' in prompts[0] and canary not in prompts[0]
