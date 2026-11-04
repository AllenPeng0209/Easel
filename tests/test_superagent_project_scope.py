"""The task panel follows its current IP URL, including mutation boundaries."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from easel.access import AccessStore, SESSION_COOKIE
from easel.projects import ProjectScopeMiddleware
from web.access_control import AccessControlMiddleware
from web.superagent_routes import create_router, task_store


pytestmark = pytest.mark.access_integration
A, B = 'a' * 16, 'b' * 16


@pytest.fixture
def tasks_http(tmp_path):
    (tmp_path / 'projects').mkdir()
    (tmp_path / 'projects/_registry.json').write_text(json.dumps([
        {'id': 'default', 'name': '默认 IP'}, {'id': A, 'name': '品牌 A'}, {'id': B, 'name': '品牌 B'},
    ]))
    access = AccessStore(tmp_path)
    actor, token = access.setup('scope-owner', 'Scope owner password 42', project_ids=['default', A, B])
    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path, lambda: None))
    app.add_middleware(ProjectScopeMiddleware, get_root=lambda: tmp_path)
    app.add_middleware(AccessControlMiddleware, get_root=lambda: tmp_path)
    client = TestClient(app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})
    client.cookies.set(SESSION_COOKIE, token)
    store = task_store(tmp_path)
    tasks = {project: store.create(actor_id=actor['id'], project_id=project,
        title='Task ' + project, brief='Own IP only', steps=[{'kind': 'draft'}])
        for project in ('default', A, B)}
    return client, store, actor, tasks


def test_default_task_list_does_not_aggregate_other_ips(tasks_http):
    client, _, _, tasks = tasks_http
    response = client.get('/api/superagent/tasks')
    assert response.status_code == 200, response.text
    assert [task['id'] for task in response.json()['tasks']] == [tasks['default']['id']]
    assert [row['id'] for row in response.json()['projects']] == ['default']


def test_scoped_task_list_contains_only_the_current_ip(tasks_http):
    client, _, _, tasks = tasks_http
    response = client.get(f'/api/projects/{A}/superagent/tasks')
    assert response.status_code == 200, response.text
    assert [task['id'] for task in response.json()['tasks']] == [tasks[A]['id']]
    assert [row['id'] for row in response.json()['projects']] == [A]


def test_legacy_default_url_cannot_create_a_task_in_another_ip(tasks_http):
    client, store, actor, _ = tasks_http
    before = len(store.list(actor_id=actor['id'], project_id=B))
    response = client.post('/api/superagent/tasks', json={
        'projectId': B, 'title': 'Wrong tab', 'brief': 'Wrong IP', 'requestId': 'cross-default'})
    assert response.status_code == 409, response.text
    assert len(store.list(actor_id=actor['id'], project_id=B)) == before


@pytest.mark.parametrize('operation,extra', [
    ('answer', {'questionId': 'c' * 32, 'answer': 'Approve'}), ('stop', {}), ('retry', {}),
])
def test_task_mutations_cannot_select_another_ip_in_the_body(tasks_http, operation, extra):
    client, store, actor, tasks = tasks_http
    response = client.post(f'/api/projects/{A}/superagent/tasks/{tasks[B]["id"]}/{operation}',
        json={'projectId': B, **extra})
    assert response.status_code == 409, response.text
    assert store.get(tasks[B]['id'], actor_id=actor['id'], project_id=B)['status'] == 'queued'


def test_publication_intake_cannot_select_a_foreign_ip(tasks_http):
    client, _, _, _ = tasks_http
    response = client.post(f'/api/projects/{A}/superagent/tasks/publish',
        json={'projectId': B, 'approvalId': 'd' * 32})
    assert response.status_code == 409, response.text


def test_new_tasks_remain_in_the_explicit_current_ip(tasks_http):
    client, store, actor, _ = tasks_http
    response = client.post(f'/api/projects/{A}/superagent/tasks', json={
        'projectId': A, 'title': 'A draft', 'brief': 'Draft only', 'requestId': 'scoped-create'})
    assert response.status_code == 200, response.text
    assert response.json()['projectId'] == A
    assert store.get(response.json()['id'], actor_id=actor['id'], project_id=A)['title'] == 'A draft'


def test_a_foreign_task_id_is_not_rebound_to_the_current_ip(tasks_http):
    client, store, actor, tasks = tasks_http
    response = client.post(f'/api/projects/{A}/superagent/tasks/{tasks[B]["id"]}/stop', json={'projectId': A})
    assert response.status_code == 404
    assert store.get(tasks[B]['id'], actor_id=actor['id'], project_id=B)['status'] == 'queued'


def test_viewer_role_remains_read_only_inside_the_current_ip(tasks_http, tmp_path):
    client, _, _, _ = tasks_http
    access = AccessStore(tmp_path)
    viewer = access.create_user('scope-viewer', 'Scope viewer password 43')
    access.set_member(A, viewer['id'], 'viewer')
    client.cookies.set(SESSION_COOKIE, access.login('scope-viewer', 'Scope viewer password 43')[1])
    response = client.get(f'/api/projects/{A}/superagent/tasks')
    assert response.status_code == 200
    assert response.json()['tasks'] == []
    assert response.json()['projects'] == [{'id': A, 'name': '品牌 A', 'canOperate': False}]
    assert client.post(f'/api/projects/{A}/superagent/tasks', json={
        'projectId': A, 'title': 'Denied', 'brief': 'Read only', 'requestId': 'denied'}).status_code == 403
    assert client.post(f'/api/projects/{A}/superagent/tasks/unknown-operation', json={}).status_code == 403
