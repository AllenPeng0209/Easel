"""C28 authorized model choices bind each admitted request without sticky overrides."""
import asyncio

import pytest

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

MODELS = [
    {'id': 'native-base', 'label': 'Native default', 'default': True},
    {'id': 'alternate-model', 'label': 'Alternate', 'default': False},
]


@pytest.fixture
def model_runtime(monkeypatch):
    config = {'runtime': 'codex', 'models': {}, 'sandbox': 'read-only'}
    monkeypatch.setattr(web, '_harness_settings', lambda: config)
    requests, catalogs = [], []

    async def catalog():
        catalogs.append(True)
        return MODELS.copy(), None

    class Adapter:
        async def run(self, request, emit, ask, bind):
            requests.append(request)
            bind('model-test-native')
            emit('token', 'answer')

    monkeypatch.setattr(web, '_read_chat_model_catalog', catalog, raising=False)
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    return config, requests, catalogs


@pytest.mark.access_integration
@pytest.mark.parametrize('native_configured,baseline,chosen', [
    (None, 'native-base', 'alternate-model'),
    ('alternate-model', 'alternate-model', 'native-base'),
    ('configured-outside-catalog', 'native-base', 'alternate-model'),
])
def test_codex_override_frozen_for_admission_replay_and_default_restore(
        artifact_http_environment, model_runtime, monkeypatch, native_configured, baseline, chosen):
    _, owner, _, _, _ = artifact_http_environment
    config, requests, catalogs = model_runtime
    original_catalog = web._read_chat_model_catalog

    async def catalog():
        models, _ = await original_catalog()
        return models, native_configured

    monkeypatch.setattr(web, '_read_chat_model_catalog', catalog)
    route = '/api/chat/models?sessionId=model-session'
    choices = owner.get(route)
    assert choices.status_code == 200, choices.text
    assert choices.json()['runtime'] == 'codex' and choices.json()['model'] == baseline
    assert choices.json()['canSelect'] is True
    assert choices.json()['options'] == [{'id': item['id'], 'label': item['label']} for item in MODELS]
    body = {'sessionId': 'model-session', 'turnId': 'first-model', 'message': 'hello'}
    first = owner.post('/api/chat/stream', json={**body, 'modelOverride': chosen})
    assert first.status_code == 200 and requests[0].model == chosen
    assert owner.get('/api/chat/last/model-session').json()['requestedModel'] == chosen
    assert owner.get(route).json()['lastRequestedModel'] == chosen
    lookups = len(catalogs)
    replay = owner.post('/api/chat/stream', json={**body, 'modelOverride': chosen})
    assert replay.status_code == 200 and len(requests) == 1 and len(catalogs) == lookups
    assert owner.post('/api/chat/stream', json={**body, 'modelOverride': baseline}).status_code == 409
    next_turn = owner.post('/api/chat/stream', json={**body, 'turnId': 'next-model'})
    assert next_turn.status_code == 200 and requests[1].model == baseline
    assert config['models'] == {}


@pytest.mark.access_integration
def test_unauthorized_or_unknown_model_does_not_admit_and_preference_is_not_authority(
        artifact_http_environment, model_runtime):
    _, owner, members, _, _ = artifact_http_environment
    _, requests, catalogs = model_runtime
    body = {'sessionId': 'selection', 'turnId': 'next', 'message': 'test'}
    assert members['viewer'].get('/api/chat/models?sessionId=selection').status_code == 403
    assert members['operator'].post('/api/chat/stream', json={**body, 'modelOverride': 'alternate-model'}).status_code == 422
    assert not catalogs
    assert owner.post('/api/chat/stream', json={**body, 'modelOverride': 'unlisted'}).status_code == 422
    assert not requests
    assert owner.put('/api/chat/sessions', json={'sessions': [
        {'id': 'selection', 'messages': [], 'nextModel': 'unlisted'}]}).status_code == 200
    assert owner.get('/api/chat/sessions').json()['sessions'][0]['nextModel'] == 'unlisted'
    assert owner.post('/api/chat/stream', json=body).status_code == 200
    assert len(requests) == 1 and requests[0].model == ''


@pytest.mark.access_integration
def test_new_admitted_model_does_not_change_running_request(artifact_http_environment, model_runtime, monkeypatch):
    root, owner, _, _, _ = artifact_http_environment
    config, requests, _ = model_runtime

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class Adapter:
            async def run(self, request, emit, ask, bind):
                requests.append(request)
                bind('native-queued-models')
                if len(requests) == 1:
                    started.set()
                    await release.wait()
                emit('token', 'complete')

        monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
        first = await web.api_chat_stream(web.ChatRequest(
            message='first', sessionId='queue-models', turnId='queue-first', modelOverride='alternate-model'))
        await asyncio.wait_for(started.wait(), 2)
        second = await web.api_chat_stream(web.ChatRequest(
            message='next', sessionId='queue-models', turnId='queue-second', modelOverride='native-base'))
        try:
            assert len(requests) == 1 and requests[0].model == 'alternate-model'
        finally:
            release.set()

        async def drain(response):
            return [frame async for frame in response.body_iterator]

        await asyncio.wait_for(asyncio.gather(drain(first), drain(second)), 3)
        assert [request.model for request in requests] == ['alternate-model', 'native-base']
        assert config['models'] == {}

    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        asyncio.run(scenario())
