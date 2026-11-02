"""Project boundaries must cover files, identities and background work."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from web import app as web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(web, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(web, "BROWSER_PROFILES", tmp_path / "browser")
    return TestClient(web.app, base_url="http://localhost:7860",
                      headers={"Origin": "http://localhost:7860"})


def create(client, name):
    response = client.post("/api/projects", json={"name": name, "description": "自媒体品牌"})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_project_lifecycle_keeps_legacy_space_and_validates_names(client):
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/projects").json()[0]["id"] == "default"
    project = create(client, "科技自媒体")
    assert project != "default"
    assert client.patch(f"/api/projects/{project}", json={"name": "科技观察"}).json()["name"] == "科技观察"
    assert len(client.get("/api/projects").json()) == 2
    assert client.post("/api/projects", json={"name": "  "}).status_code == 400
    # The authentication boundary rejects malformed IP identifiers before any
    # registry lookup, so they cannot be used to probe another space.
    assert client.get("/api/projects/missing/ideas").status_code == 403


def test_same_named_content_and_schedule_are_isolated(client, tmp_path):
    a, b = create(client, "房产"), create(client, "汽车")
    for project, title in [(a, "房产选题"), (b, "汽车选题")]:
        base = f"/api/projects/{project}"
        assert client.post(base + "/ideas", json={"title": title}).status_code == 200
        assert client.post(base + "/schedule", json={"title": title, "date": "2026-11-02"}).status_code == 200
    assert client.get(f"/api/projects/{a}/ideas").json()[0]["title"] == "房产选题"
    assert client.get(f"/api/projects/{b}/schedule").json()[0]["title"] == "汽车选题"
    assert client.get("/api/ideas").json() == []
    assert not (tmp_path / "outputs" / "_ideas.json").exists()


def test_accounts_cookies_and_logout_belong_to_project(client, tmp_path):
    a, b = create(client, "A"), create(client, "B")
    # Real login markers and cookies; never start or log into a social service.
    for project, content in [(a, "a-cookie"), (b, "b-cookie")]:
        root = tmp_path / "projects" / project
        (root / "cookies.json").write_text(content)
        login = root / "outputs" / "_login"
        login.mkdir(parents=True)
        (login / "xiaohongshu.json").write_text(json.dumps({"state": "success"}))
    accounts = client.get(f"/api/projects/{a}/accounts").json()
    assert next(x for x in accounts if x["platform"] == "bilibili")["loggedIn"]
    assert client.post(f"/api/projects/{a}/logout/bilibili").status_code == 200
    assert not (tmp_path / "projects" / a / "cookies.json").exists()
    assert (tmp_path / "projects" / b / "cookies.json").read_text() == "b-cookie"
    assert all(not x["loggedIn"] for x in client.get("/api/accounts").json())


def test_media_and_chat_replay_never_fall_back_to_other_space(client, tmp_path):
    a, b = create(client, "A"), create(client, "B")
    root = tmp_path / "projects" / a / "outputs"
    (root / "topic").mkdir()
    (root / "topic" / "draft.txt").write_text("A only")
    assert client.get(f"/api/projects/{a}/media/topic/draft.txt").text == "A only"
    assert client.get(f"/api/projects/{b}/media/topic/draft.txt").status_code == 404
    from easel.projects import project_context
    with project_context(a, tmp_path):
        web._save_turn("web:same-session", "done", "private reply")
    assert client.get(f"/api/projects/{a}/chat/last/same-session").json()["text"] == "private reply"
    assert client.get(f"/api/projects/{b}/chat/last/same-session").json().get("text", "") == ""


def test_concurrent_requests_and_background_threads_keep_original_project(client, tmp_path):
    a, b = create(client, "A"), create(client, "B")
    from easel.projects import project_context, context_thread
    threads = []
    for project in [a, b]:
        with project_context(project, tmp_path):
            thread = context_thread(target=lambda: web._write_ideas([{"title": "own"}]))
            threads.append(thread)
            thread.start()
    for thread in threads:
        thread.join()
    with ThreadPoolExecutor() as pool:
        responses = list(pool.map(lambda p: client.get(f"/api/projects/{p}/ideas").json(), [a, b]))
    assert responses == [[{"title": "own"}], [{"title": "own"}]]
    assert not (tmp_path / "outputs" / "_ideas.json").exists()


def test_runtime_identity_cache_and_agent_context_are_scoped(client, tmp_path):
    a, b = create(client, "A"), create(client, "B")
    from easel.projects import project_context
    with project_context(a, tmp_path):
        web._WHOAMI_CACHE["xiaohongshu"] = (0, {"name": "A"})
        transcript_a = web._openclaw_session_id("same")
        env = web._proxy_env()
        assert env["EASEL_ROOT"] == str(tmp_path / "projects" / a)
        assert env["EASEL_BROWSER_PROFILES"] == str(tmp_path / "projects" / a / "browser")
    with project_context(b, tmp_path):
        assert web._WHOAMI_CACHE.get("xiaohongshu") is None
        assert web._openclaw_session_id("same") != transcript_a


def test_project_agent_uses_local_workspace_and_never_shared_gateway(client, tmp_path, monkeypatch):
    project = create(client, "A")
    original = tmp_path / "model-config.json"
    original.write_text(json.dumps({"agents": {"defaults": {"workspace": "/shared"}},
                                    "models": {"providers": {}}, "channels": {"test": {}}}))
    monkeypatch.setattr(web, "_oc_config_path", lambda: original)
    from easel.projects import project_context
    with project_context(project, tmp_path):
        command, env = web._project_agent_command(
            ["openclaw", "--profile", "easel", "agent", "--session-key", "agent:main:same", "--message", "hello"], {})
        assert "--local" in command
        assert "--session-key" not in command
        assert "--profile" not in command
        assert web._resolve_transport("same") == "cli"
        config = json.loads(__import__('pathlib').Path(env["OPENCLAW_CONFIG_PATH"]).read_text())
        assert config["agents"]["defaults"]["workspace"] == str(tmp_path / "projects" / project)
        assert env["OPENCLAW_STATE_DIR"] == str(tmp_path / "projects" / project / "_openclaw")
        assert not config.get("channels")
    assert json.loads(original.read_text())["agents"]["defaults"]["workspace"] == "/shared"


def test_stop_cannot_cancel_same_session_in_another_project(client, tmp_path):
    a, b = create(client, "A"), create(client, "B")
    from easel.projects import project_context
    cancelled = []
    with project_context(a, tmp_path):
        web._RUNNING_CHAT["same"] = SimpleNamespace(terminate=lambda: cancelled.append("A"))
    result = client.post(f"/api/projects/{b}/chat/stop", json={"sessionId": "same"})
    assert result.status_code == 200
    assert not result.json()["stopped"]
    assert cancelled == []
