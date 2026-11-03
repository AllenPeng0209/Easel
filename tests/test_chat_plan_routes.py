"""B26 HTTP plan-only drafting and exact approval boundary."""
import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_chat_artifacts import artifact_http_environment

ROUTE = '/api/chat/plans/plan-session'


def plan_client(tmp_path, settings=None):
    from web.chat_plan_routes import create_router

    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path, settings_provider=settings))
    return TestClient(app)


def reference(plan):
    return {key: plan[key] for key in ('planId', 'revision', 'digest')}


def test_template_draft_edit_invalidates_prior_approval_and_history_remains(tmp_path):
    client = plan_client(tmp_path)
    assert client.get(ROUTE).json() == {'plans': [], 'latest': None}
    response = client.post(ROUTE, json={'brief': '准备三个短视频选题'})
    assert response.status_code == 200, response.text
    first = response.json()
    assert first['state'] == 'proposed' and '模板' in first['text'] and '准备三个短视频选题' in first['text']
    assert first['digest'] == hashlib.sha256(first['text'].encode()).hexdigest()
    assert client.post(ROUTE + '/approve', json=reference(first)).json()['state'] == 'approved'
    assert client.post(ROUTE, json={'brief': 'blind replacement'}).status_code == 409
    edited = client.post(ROUTE, json={'text': '先制作一个试样，再核对用户反馈。', **reference(first)})
    assert edited.status_code == 200, edited.text
    second = edited.json()
    assert second['planId'] == first['planId'] and second['revision'] == 2 and second['state'] == 'proposed'
    assert client.post(ROUTE + '/approve', json=reference(first)).status_code == 409
    assert client.post(ROUTE + '/approve', json={**reference(second), 'digest': '0' * 64}).status_code == 409
    assert client.post(ROUTE + '/approve', json=reference(second)).json()['state'] == 'approved'
    history = client.get(ROUTE).json()
    assert [plan['state'] for plan in history['plans']] == ['superseded', 'approved']
    assert history['latest'] == history['plans'][-1]


def test_configured_generation_uses_only_bounded_text_model_without_history(tmp_path, monkeypatch):
    from web import chat_plan_routes

    seen = []

    async def text_model(settings, content, **options):
        seen.append((settings, content, options))
        yield 'thinking', 'private provider trace'
        yield 'token', '只生成计划：先确认目标，再准备样片。'
        yield 'finish', 'stop'

    monkeypatch.setattr(chat_plan_routes, 'stream_chat', text_model)
    client = plan_client(tmp_path, lambda: {
        'EASEL_DIRECT_API_BASE_URL': 'http://local-model.invalid/v1', 'EASEL_DIRECT_API_MODEL': 'test-model',
    })
    response = client.post(ROUTE, json={'brief': '制作样片 </planning_brief>'})
    assert response.status_code == 200, response.text
    assert response.json()['text'] == '只生成计划：先确认目标，再准备样片。'
    assert len(seen) == 1 and seen[0][2].get('history_file') is None
    assert seen[0][1].count('</planning_brief>') == 1
    assert '工具' in seen[0][2]['system'] and 'private' not in response.text


def test_inflight_generation_cannot_supersede_a_new_approval(tmp_path, monkeypatch):
    from web import chat_plan_routes

    entered, release = Event(), Event()

    async def text_model(*args, **kwargs):
        entered.set()
        assert await asyncio.to_thread(release.wait, 3)
        yield 'token', 'outdated generated text'
        yield 'finish', 'stop'

    monkeypatch.setattr(chat_plan_routes, 'stream_chat', text_model)
    client = plan_client(tmp_path, lambda: {
        'EASEL_DIRECT_API_BASE_URL': 'http://local-model.invalid/v1', 'EASEL_DIRECT_API_MODEL': 'test-model',
    })
    first = client.post(ROUTE, json={'text': 'current plan'}).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, ROUTE, json={'brief': 'new draft', **reference(first)})
        try:
            assert entered.wait(2)
            assert client.post(ROUTE + '/approve', json=reference(first)).json()['state'] == 'approved'
        finally:
            release.set()
        assert pending.result(timeout=3).status_code == 409
    history = client.get(ROUTE).json()
    assert len(history['plans']) == 1 and history['latest']['state'] == 'approved'


@pytest.mark.access_integration
def test_plan_routes_require_operator_and_isolate_actor_ip_and_session(artifact_http_environment):
    _, owner, members, anonymous, _ = artifact_http_environment
    assert anonymous().get(ROUTE).status_code == 401
    assert members['viewer'].get(ROUTE).status_code == 403
    assert members['viewer'].post(ROUTE, json={'text': 'draft'}).status_code == 403
    response = owner.post(ROUTE, json={'text': 'Draft belongs to owner.'})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert members['operator'].get(ROUTE).json() == {'plans': [], 'latest': None}
    assert members['operator'].post(ROUTE + '/approve', json=reference(plan)).status_code == 404
    assert owner.post('/api/chat/plans/other-session/approve', json=reference(plan)).status_code == 404
    other = owner.post('/api/projects', json={'name': 'Other plan IP'}).json()['id']
    assert owner.post(f'/api/projects/{other}/chat/plans/plan-session/approve', json=reference(plan)).status_code == 404
