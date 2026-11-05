"""Removed planning references cannot silently admit stale chat work."""
import json
import pytest
from tests.test_chat_artifacts import artifact_http_environment
import web.app as web

pytestmark = pytest.mark.access_integration


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


@pytest.mark.parametrize('endpoint', ['/api/chat', '/api/chat/stream'])
@pytest.mark.parametrize('field', ['topicRef', 'topic_ref'])
def test_removed_topic_reference_is_rejected_before_model_execution(artifact_http_environment, monkeypatch, endpoint, field):
    root, owner, _, _, _ = artifact_http_environment
    prompts = install_runner(monkeypatch, 'api')
    response = owner.post(endpoint, json={'message': 'Continue this old topic',
        field: {'id': 'saved-topic', 'snapshotHash': 'a' * 64}})
    assert response.status_code == 422, response.text
    assert not prompts


def test_removed_planning_endpoints_do_not_modify_legacy_ideas(artifact_http_environment):
    root, owner, _, _, _ = artifact_http_environment
    legacy = root / 'outputs/_ideas.json'
    legacy.write_text(json.dumps([{'id': 'saved-topic', 'title': 'Preserved history'}]))
    before = legacy.read_bytes()
    for endpoint in ['/api/ideas', '/api/trends', '/api/trends/sources', '/api/trends/manage']:
        assert owner.get(endpoint).status_code in {403, 404}
        assert owner.post(endpoint, json={'title': 'No new planning work'}).status_code in {403, 404, 405}
    assert legacy.read_bytes() == before
