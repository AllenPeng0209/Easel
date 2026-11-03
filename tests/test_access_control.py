"""Authorization regressions exercise the HTTP boundary, not hidden UI buttons."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse


def boundary(root: Path, *, auth_routes: bool = False, during_stream=None):
    app = FastAPI()
    if auth_routes:
        from web.access_routes import create_router
        app.include_router(create_router(lambda: root, lambda: [
            {"id": "default", "name": "默认 IP"},
            {"id": "aaaaaaaaaaaaaaaa", "name": "汽车观察"},
            {"id": "bbbbbbbbbbbbbbbb", "name": "城市生活"},
        ]))
    try:
        from web.access_control import AccessControlMiddleware
    except ModuleNotFoundError:
        AccessControlMiddleware = None
    if AccessControlMiddleware is not None:
        app.add_middleware(AccessControlMiddleware, get_root=lambda: root)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def resource(request: Request, path: str):
        if during_stream is not None and path.endswith("/chat/stream"):
            async def events():
                yield b'data: "first reply"\n\n'
                during_stream()
                yield b'data: "private reply after revocation"\n\n'
            return StreamingResponse(events(), media_type="text/event-stream")
        return {"path": path, "user": getattr(request.state, "user", None),
                "role": getattr(request.state, "project_role", None)}

    return TestClient(app, base_url="http://localhost:7860",
                      headers={"Origin": "http://localhost:7860"}, client=("127.0.0.1", 40123))


def test_loopback_and_forged_user_headers_do_not_replace_login(tmp_path):
    client = boundary(tmp_path)
    response = client.get("/api/ideas", headers={"X-Easel-User": "owner", "X-Easel-Role": "owner"})
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.fixture
def users(tmp_path):
    owner = boundary(tmp_path, auth_routes=True)
    response = owner.post("/api/auth/setup", json={
        "username": "admin", "display_name": "管理员", "password": "a-password-for-tests-42",
    })
    assert response.status_code == 200, response.text
    admin = response.json()["user"]
    created = {}
    for username in ("owner", "operator", "viewer", "outsider"):
        response = owner.post("/api/users", json={"username": username, "display_name": username,
                                                 "password": "a-password-for-tests-42"})
        assert response.status_code == 200, response.text
        created[username] = response.json()
    for username in ("owner", "operator", "viewer"):
        response = owner.post("/api/projects/aaaaaaaaaaaaaaaa/members", json={
            "user_id": created[username]["id"], "role": username,
        })
        assert response.status_code == 200, response.text
    clients = {"admin": owner}
    for username in created:
        client = boundary(tmp_path, auth_routes=True)
        result = client.post("/api/auth/login", json={"username": username, "password": "a-password-for-tests-42"})
        assert result.status_code == 200, result.text
        clients[username] = client
    return clients, {**created, "admin": admin, "_root": tmp_path}


@pytest.mark.parametrize("path", [
    "/api/projects/aaaaaaaaaaaaaaaa/ideas",
    "/api/projects/aaaaaaaaaaaaaaaa/accounts",
    "/api/projects/aaaaaaaaaaaaaaaa/chat/last/same-id",
    "/api/projects/aaaaaaaaaaaaaaaa/media/topic/report.txt",
])
def test_membership_is_required_for_each_ip_including_read_apis(users, path):
    clients, _ = users
    assert clients["outsider"].get(path).status_code == 403
    assert clients["viewer"].get(path).status_code == 200
    assert clients["viewer"].get(path.replace("aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb")).status_code == 403


def test_legacy_default_alias_requires_its_own_membership(users):
    clients, _ = users
    assert clients["viewer"].get("/api/ideas").status_code == 403
    assert clients["viewer"].get("/api/projects/default/ideas").status_code == 403
    assert clients["admin"].get("/api/ideas").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("POST", "ideas"), ("PUT", "schedule/existing"), ("DELETE", "output/topic/report.txt"),
    ("POST", "chat/stream"), ("POST", "chat/stop"), ("POST", "upload"), ("POST", "upload/local"),
    ("POST", "publish/douyin"), ("POST", "manual-publish/douyin/export"),
    ("PATCH", "interactions/drafts/existing"), ("POST", "workshop/projects"),
    ("GET", "accounts/douyin/whoami"), ("GET", "analytics/douyin"),
    ("GET", "unknown-future-api"),
])
def test_viewer_cannot_execute_mutations_or_side_effecting_gets(users, method, path):
    clients, _ = users
    result = clients["viewer"].request(method, "/api/projects/aaaaaaaaaaaaaaaa/" + path)
    assert result.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("POST", "ideas"), ("PUT", "schedule/existing"), ("POST", "chat/stream"),
    ("POST", "publish/douyin"), ("GET", "analytics/douyin"), ("POST", "upload/local"),
])
def test_operator_can_perform_content_operations(users, method, path):
    clients, _ = users
    assert clients["operator"].request(method, "/api/projects/aaaaaaaaaaaaaaaa/" + path).status_code == 200


@pytest.mark.parametrize("method,path", [
    ("POST", "login/douyin"), ("GET", "login/douyin/status"), ("POST", "logout/douyin"),
    ("GET", "accounts/wechat-oa/credentials"), ("POST", "accounts/wechat-oa/mp-login"),
    ("GET", "accounts/wechat-oa/mp-login/status"),
    ("POST", "interactions/channels"), ("PATCH", "interactions/channels/existing"),
])
def test_account_connection_is_owner_only(users, method, path):
    clients, _ = users
    target = "/api/projects/aaaaaaaaaaaaaaaa/" + path
    assert clients["operator"].request(method, target).status_code == 403
    assert clients["owner"].request(method, target).status_code == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/settings/models"), ("POST", "/api/settings/models/save"),
    ("POST", "/api/gateway/restart"), ("POST", "/api/env/install"),
    ("GET", "/api/projects/aaaaaaaaaaaaaaaa/env/tools"),
    ("POST", "/api/projects/aaaaaaaaaaaaaaaa/skill"),
])
def test_ip_owner_is_not_a_platform_administrator(users, method, path):
    clients, _ = users
    assert clients["owner"].request(method, path).status_code == 403
    assert clients["admin"].request(method, path).status_code == 200


def test_revocation_and_stale_tab_identity_take_effect_on_the_next_request(users):
    clients, people = users
    target = "/api/projects/aaaaaaaaaaaaaaaa/ideas"
    client = clients["operator"]
    assert client.get(target, headers={"X-Easel-User": people["operator"]["id"]}).status_code == 200
    assert client.get(target, headers={"X-Easel-User": people["viewer"]["id"]}).status_code == 401
    assert clients["admin"].patch("/api/projects/aaaaaaaaaaaaaaaa/members/" + people["operator"]["id"],
                                   json={"role": "viewer"}).status_code == 200
    assert client.post(target).status_code == 403


@pytest.mark.parametrize("path", [
    "media/_login/douyin.png", "output/_debug/chat-stream.jsonl", "media/_sessions/api/private.json",
    "media/topic/%2e%2e/_login/douyin.png", "media/topic/%252e%252e/_login/douyin.png",
    "media/topic%5c..%5c_login%5cdouyin.png", "output/.hidden/token.json",
])
def test_viewer_cannot_read_system_files_through_encoded_paths(users, path):
    clients, _ = users
    assert clients["viewer"].get("/api/projects/aaaaaaaaaaaaaaaa/" + path).status_code == 403


def test_authenticated_writes_require_same_origin_and_do_not_trust_identity_headers(users):
    clients, _ = users
    target = "/api/projects/aaaaaaaaaaaaaaaa/ideas"
    assert clients["owner"].post(target, headers={"Origin": "https://evil.example"}).status_code == 403
    assert clients["owner"].post(target, headers={"Origin": "null"}).status_code == 403
    assert clients["owner"].post(target, headers={"Origin": "http://localhost:7860", "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert clients["owner"].post(target, headers={"Origin": "http://localhost:7860"}).status_code == 200


def test_cross_origin_same_site_images_cannot_trigger_account_refresh(users):
    clients, _ = users
    # SameSite cookies do not protect one localhost port from another localhost
    # port, nor one subdomain from another. Browser metadata closes that gap.
    response = clients["owner"].get("/api/projects/aaaaaaaaaaaaaaaa/accounts/douyin/whoami",
                                     headers={"Sec-Fetch-Site": "same-site"})
    assert response.status_code == 403


def test_an_open_chat_stream_stops_disclosing_tokens_after_role_is_revoked(users):
    from easel.access import AccessStore
    clients, people = users
    store = AccessStore(people["_root"])
    def revoke():
        store.set_member("aaaaaaaaaaaaaaaa", people["operator"]["id"], "viewer", actor_id=people["admin"]["id"])
    client = boundary(people["_root"], during_stream=revoke)
    client.cookies.update(clients["operator"].cookies)
    response = client.post("/api/projects/aaaaaaaaaaaaaaaa/chat/stream")
    assert response.status_code == 200
    assert "first reply" in response.text
    assert "private reply after revocation" not in response.text
    assert "event: error" in response.text


def test_operator_can_upload_a_video_above_the_normal_limit_using_multipart(users, monkeypatch):
    from web import app as web
    from web.access_control import AccessControlMiddleware
    from easel.projects import ProjectScopeMiddleware

    clients, people = users
    root = people["_root"]
    registry = root / "projects" / "_registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps([{"id": "aaaaaaaaaaaaaaaa", "name": "汽车观察"}]))
    app = FastAPI()
    app.add_api_route("/api/upload", web.api_upload, methods=["POST"])
    app.add_api_route("/api/upload/local", web.api_upload_local, methods=["POST"])
    app.add_middleware(ProjectScopeMiddleware, get_root=lambda: root)
    app.add_middleware(AccessControlMiddleware, get_root=lambda: root)
    client = TestClient(app, base_url="http://localhost:7860", headers={"Origin": "http://localhost:7860"})
    client.cookies.update(clients["operator"].cookies)
    monkeypatch.setattr(web, "MAX_UPLOAD_MB", 1)
    video = b"video-test-header" + bytes(2 * 1024 * 1024)
    fields = {"sessionId": "large-video-upload"}
    normal = client.post("/api/projects/aaaaaaaaaaaaaaaa/upload", data=fields,
                         files={"files": ("clip.mp4", video, "video/mp4")})
    assert normal.status_code == 413
    result = client.post("/api/projects/aaaaaaaaaaaaaaaa/upload/local", data=fields,
                         files={"files": ("clip.mp4", video, "video/mp4")})
    assert result.status_code == 200, result.text
    path = result.json()["files"][0]["path"]
    assert path.startswith("_inbox/" + people["operator"]["id"] + "-")
    saved = root / "projects" / "aaaaaaaaaaaaaaaa" / "outputs" / path
    assert saved.read_bytes() == video
    # A JSON host path is never a supported input to this transport.
    assert client.post("/api/projects/aaaaaaaaaaaaaaaa/upload/local", json={"path": "/etc/passwd"}).status_code == 422
    client.cookies.update(clients["viewer"].cookies)
    denied = client.post("/api/projects/aaaaaaaaaaaaaaaa/upload/local", data=fields,
                         files={"files": ("viewer.mp4", video, "video/mp4")})
    assert denied.status_code == 403
    assert not list((root / "projects" / "aaaaaaaaaaaaaaaa" / "outputs").rglob("viewer.mp4"))


@pytest.mark.parametrize(("method", "resource"), [
    ("GET", "attachment-search"),
    ("POST", "attachment-select"),
])
def test_chat_attachment_picker_requires_operator_access(users, method, resource):
    clients, _ = users
    path = f"/api/projects/aaaaaaaaaaaaaaaa/chat/{resource}"
    assert clients["viewer"].request(method, path).status_code == 403
    assert clients["outsider"].request(method, path).status_code == 403
    assert clients["operator"].request(method, path).status_code == 200
    assert clients["operator"].request("POST" if method == "GET" else "GET", path).status_code == 403
