"""B26 exact plan admission across the production chat stream route."""
import pytest

from tests.test_chat_artifacts import artifact_http_environment
from tests.test_chat_topic_reference import install_runner

pytestmark = pytest.mark.access_integration
PLAN = '/api/chat/plans/production-plan'


def reference(plan):
    return {key: plan[key] for key in ('planId', 'revision', 'digest')}


def execution(plan):
    return {'sessionId': 'production-plan', 'turnId': f"plan-{plan['planId']}-{plan['revision']}",
            'message': plan['text'], 'planRef': reference(plan)}


def test_plan_is_only_dispatched_after_exact_latest_review(artifact_http_environment, monkeypatch):
    _, owner, members, _, _ = artifact_http_environment
    calls = install_runner(monkeypatch, 'codex')
    first = owner.post(PLAN, json={'text': '计划一：制作三份草稿'}).json()
    assert owner.post('/api/chat/stream', json=execution(first)).status_code == 409
    assert not calls
    def history(plan):
        return {'sessions': [{'id': 'production-plan', 'updated': 10, 'messages': [{
            'role': 'user', 'content': plan['text'], 'planRef': reference(plan),
            'clientTurnId': execution(plan)['turnId'],
        }]}]}
    assert owner.put('/api/chat/sessions', json=history(first)).status_code == 400
    assert owner.post(PLAN + '/approve', json=reference(first)).status_code == 200
    latest = owner.post(PLAN, json={'text': '计划二：只制作一份草稿', **reference(first)}).json()
    assert owner.post('/api/chat/stream', json=execution(first)).status_code == 409
    assert owner.post(PLAN + '/approve', json=reference(latest)).status_code == 200
    request = execution(latest)
    assert members['operator'].post('/api/chat/stream', json=request).status_code == 404
    assert owner.post('/api/chat/stream', json={**request, 'message': 'changed text'}).status_code == 409
    assert owner.post('/api/chat/stream', json={**request, 'turnId': 'another-turn'}).status_code == 422
    assert owner.post('/api/chat', json=request).status_code == 409
    assert not calls
    result = owner.post('/api/chat/stream', json=request)
    assert result.status_code == 200 and 'event: admitted' in result.text
    assert owner.post('/api/chat/stream', json=request).status_code == 200
    assert len(calls) == 1
    assert latest['text'] in calls[0] and '公开发布' in calls[0]
    stored = owner.get(PLAN).json()['latest']
    assert stored['state'] == 'claimed' and stored['executionTurnId'] == request['turnId']
    assert owner.put('/api/chat/sessions', json=history(latest)).status_code == 200
