import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlmodel import select

from easel.access import AccessStore, SESSION_COOKIE
from easel.operations import OperationsManager
from easel.operations_web import OperationsMount, interaction_path
from easel.projects import ProjectScopeMiddleware, create_router as project_router
from web.access_control import AccessControlMiddleware

pytestmark = pytest.mark.access_integration


def native(tmp_path, *, raise_errors=True):
    manager = OperationsManager(tmp_path)
    access = AccessStore(tmp_path)
    _, token = access.setup(
        "native-admin", "native-password-123", project_ids=["default"]
    )
    app = FastAPI()
    app.include_router(project_router(lambda: tmp_path))
    app.mount("/api/operations", OperationsMount(lambda: manager))
    app.add_middleware(ProjectScopeMiddleware, get_root=lambda: tmp_path)
    app.add_middleware(AccessControlMiddleware, get_root=lambda: tmp_path)
    client = TestClient(
        app,
        base_url="http://127.0.0.1:7860",
        headers={"Origin": "http://127.0.0.1:7860"},
        raise_server_exceptions=raise_errors,
    )
    client.cookies.set(SESSION_COOKIE, token)
    return client, manager, access


def test_full_workbench_and_noninteraction_api_are_unavailable(tmp_path):
    client, manager, _ = native(tmp_path)
    assert client.get("/api/operations/workbench").status_code == 410
    for path in (
        "/static/app.js",
        "/api/monitors",
        "/api/collections",
        "/library",
        "/api/publish",
    ):
        assert client.get("/api/operations" + path).status_code == 404
    assert manager.runtimes == {}
    assert client.get("/api/operations/api/accounts").json() == []


def test_old_native_login_starts_cannot_bypass_the_unified_session_fence(tmp_path):
    paths = ['/api/login/browser/start', '/api/login/xhs/start', '/api/accounts/1/relogin/start']
    for path in paths:
        assert not interaction_path('POST', path), 'Raw native login can revive a revoked account'
    client, manager, _ = native(tmp_path)
    for path in paths:
        assert client.post('/api/operations' + path).status_code == 404
    assert manager.runtimes == {}


def test_unified_login_routes_start_and_poll_the_owned_runtime(tmp_path, monkeypatch):
    client, manager, _ = native(tmp_path)
    client.get('/api/operations/api/accounts')
    runtime = manager.runtimes['default']
    monkeypatch.setattr(runtime, 'start', AsyncMock())
    calls = []

    async def start(platform, account_id=None, new_account=False):
        calls.append((platform, account_id, new_account))
        return {'task_id': 'unified-task', 'status': 'opening'}

    runtime.connections = SimpleNamespace(start=start, poll=lambda task_id: {
        'task_id': task_id, 'status': 'confirmed', 'completion': 'partial',
        'capabilities': {'comments': 'verified', 'creator': 'needs_verification'},
    })
    result = client.post('/api/operations/api/accounts/connect/start?platform=xhs&account_id=2')
    assert result.status_code == 200, result.text
    assert calls == [('xhs', 2, False)]
    polled = client.get('/api/operations/api/accounts/connect/poll?task_id=unified-task')
    assert polled.status_code == 200, polled.text
    assert polled.json()['capabilities']['creator'] == 'needs_verification'
    assert 'storage_state' not in polled.json()
    created = client.post('/api/operations/api/accounts/connect/start?platform=douyin')
    assert created.status_code == 200
    assert calls[-1] == ('douyin', None, True)


def test_native_dm_write_requires_approval(tmp_path):
    client, _, _ = native(tmp_path)
    response = client.post(
        "/api/operations/api/account-actions",
        json={
            "account_id": 1,
            "action": "send_dm",
            "target_uid": "peer",
            "content": "reply",
        },
    )
    assert response.status_code == 409
    assert "审核" in response.text


def test_collection_and_download_requests_cannot_start_a_runtime(tmp_path):
    client, manager, _ = native(tmp_path)
    for path in (
        "/api/collections",
        "/api/publish",
        "/api/share-download",
        "/library/import",
    ):
        assert client.post("/api/operations" + path, json={}).status_code == 404
    assert manager.runtimes == {}


def test_viewer_cannot_launch_login_or_mutate_native_tasks(tmp_path):
    client, manager, access = native(tmp_path)
    user = access.create_user("native-viewer", "viewer-password-123")
    access.set_member("default", user["id"], "viewer")
    client.cookies.set(
        SESSION_COOKIE, access.login("native-viewer", "viewer-password-123")[1]
    )
    assert client.get("/api/operations/status").status_code == 200
    assert client.post("/api/operations/api/login/browser/start").status_code == 403
    assert (
        client.post("/api/operations/api/account-actions", json={}).status_code == 403
    )
    assert manager.runtimes == {}


def test_dm_approval_binds_exact_reply_and_replays_the_same_task(tmp_path, monkeypatch):
    client, manager, _ = native(tmp_path)
    client.get("/api/operations/api/accounts")
    runtime = manager.runtimes["default"]
    monkeypatch.setattr(runtime, "start", AsyncMock())
    with runtime.module.get_session() as db:
        db.add(
            runtime.module.DouyinAccount(id=1, platform="douyin", storage_state="{}")
        )
        db.commit()
    body = {
        "account_id": 1,
        "action": "send_dm",
        "target_uid": "peer",
        "content": "approved reply",
        "run_now": False,
    }

    def approve(payload):
        result = client.post(
            "/api/operations/approvals",
            json={"method": "POST", "path": "/api/account-actions", "body": payload},
        )
        assert result.status_code == 200, result.text
        record = result.json()
        assert (
            client.post(
                "/api/operations/approvals/" + record["id"],
                json={"approved": True, "payloadHash": record["payloadHash"]},
            ).status_code
            == 200
        )
        return {"X-Easel-Operation-Approval": record["id"]}

    headers = approve(body)
    assert (
        client.post(
            "/api/operations/api/account-actions",
            headers=headers,
            json={**body, "content": "changed"},
        ).status_code
        == 409
    )
    headers = approve(body)
    first = client.post(
        "/api/operations/api/account-actions", headers=headers, json=body
    )
    second = client.post(
        "/api/operations/api/account-actions", headers=headers, json=body
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    with runtime.module.get_session() as db:
        assert len(db.exec(select(runtime.module.AccountActionTask)).all()) == 1
    asyncio.run(manager.close())


def test_nondefault_interaction_accounts_stay_in_the_original_ip(tmp_path):
    client, manager, _ = native(tmp_path)
    project = client.post("/api/projects", json={"name": "second IP"}).json()["id"]
    base = "/api/projects/" + project + "/operations"
    assert client.get(base + "/api/accounts").json() == []
    runtime = manager.runtimes[project]
    with runtime.module.get_session() as db:
        db.add(
            runtime.module.DouyinAccount(id=1, platform="douyin", nickname="second IP")
        )
        db.commit()
    assert client.get(base + "/api/accounts").json()[0]["nickname"] == "second IP"
    assert client.get("/api/operations/api/accounts").json() == []


@pytest.mark.parametrize("content_type", [None, "application/vnd.easel+json"])
def test_approval_cannot_be_reused_for_hidden_json_body(
    tmp_path, monkeypatch, content_type
):
    import json

    client, manager, _ = native(tmp_path)
    client.get("/api/operations/api/accounts")
    runtime = manager.runtimes["default"]
    monkeypatch.setattr(runtime, "start", AsyncMock())
    with runtime.module.get_session() as db:
        db.add(
            runtime.module.DouyinAccount(id=1, platform="douyin", storage_state="{}")
        )
        db.commit()
    grant = client.post(
        "/api/operations/approvals",
        json={"method": "POST", "path": "/api/account-actions", "body": {}},
    ).json()
    assert (
        client.post(
            "/api/operations/approvals/" + grant["id"],
            json={"approved": True, "payloadHash": grant["payloadHash"]},
        ).status_code
        == 200
    )
    headers = {"X-Easel-Operation-Approval": grant["id"]}
    if content_type:
        headers["Content-Type"] = content_type
    result = client.post(
        "/api/operations/api/account-actions",
        headers=headers,
        content=json.dumps(
            {
                "account_id": 1,
                "action": "send_dm",
                "target_uid": "peer",
                "content": "unapproved body",
                "run_now": False,
            }
        ),
    )
    assert result.status_code == 409, result.text
    with runtime.module.get_session() as db:
        assert db.exec(select(runtime.module.AccountActionTask)).all() == []


def test_engine_start_failure_finishes_approval_without_locking_the_account(
    tmp_path, monkeypatch
):
    from easel.host_state import publication_state_directory
    from easel.publication_approvals import PublicationApprovalStore

    client, manager, access = native(tmp_path, raise_errors=False)
    client.get("/api/operations/api/accounts")
    runtime = manager.runtimes["default"]
    monkeypatch.setattr(
        runtime, "start", AsyncMock(side_effect=RuntimeError("fixture startup failure"))
    )
    with runtime.module.get_session() as db:
        db.add(
            runtime.module.DouyinAccount(id=1, platform="douyin", storage_state="{}")
        )
        db.commit()
    body = {
        "account_id": 1,
        "action": "send_dm",
        "target_uid": "peer",
        "content": "reviewed reply",
        "run_now": False,
    }
    grant = client.post(
        "/api/operations/approvals",
        json={"method": "POST", "path": "/api/account-actions", "body": body},
    ).json()
    assert (
        client.post(
            "/api/operations/approvals/" + grant["id"],
            json={"approved": True, "payloadHash": grant["payloadHash"]},
        ).status_code
        == 200
    )
    result = client.post(
        "/api/operations/api/account-actions",
        headers={"X-Easel-Operation-Approval": grant["id"]},
        json=body,
    )
    assert result.status_code == 503, result.text
    actor = access.authenticate_token(client.cookies.get(SESSION_COOKIE))
    store = PublicationApprovalStore(
        publication_state_directory(tmp_path, "default") / "operations"
    )
    record = store.get(grant["id"], actor_id=actor["id"], project_id="default")
    assert record["result"]["status"] == "failed"
    assert record["result"]["notStarted"] is True
    assert not store.profile_busy("operations-1", project_id="default")


def test_account_login_poll_is_available_in_the_current_ip(tmp_path):
    client, manager, _ = native(tmp_path)
    client.get('/api/operations/api/accounts')
    runtime = manager.runtimes['default']
    runtime.module.login_tasks['fixture-login'] = {'status':'waiting','platform':'douyin','creator':False,'account_id':None}
    result = client.get('/api/operations/api/login/browser/poll',params={'task_id':'fixture-login'})
    assert result.status_code == 200,result.text
    assert result.json()['status'] == 'waiting'
    project=client.post('/api/projects',json={'name':'another login space'}).json()['id']
    other=client.get(f'/api/projects/{project}/operations/api/login/browser/poll',params={'task_id':'fixture-login'})
    assert other.status_code == 404
