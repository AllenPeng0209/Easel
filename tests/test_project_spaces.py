"""Project boundaries must cover files, identities and background work."""
import json
import io
import zipfile
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
    for name, relative in {
        'LOGIN_DIR': 'outputs/_login',
        'PUBLISH_DIR': 'outputs/_publish',
        'PROFILE_BUILD_DIR': 'outputs/_profile_build',
        'DEBUG_DIR': 'outputs/_debug',
        'SESSIONS_DIR': 'outputs/_sessions',
        'SCHEDULE_FILE': 'outputs/_schedule.json',
        'IDEAS_FILE': 'outputs/_ideas.json',
        'WECHAT_CONFIG_YAML': 'wechat-publisher.yaml',
        'OPENCLAW_SESSIONS_DIR': '_openclaw/agents/main/sessions',
    }.items():
        # Default-IP compatibility paths are fixed at module import; patch them
        # independently so this fixture never reads the developer's accounts.
        monkeypatch.setattr(web, name, tmp_path / relative)
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


def three_spaces(client, tmp_path):
    identifiers = ['default', create(client, '内容 A'), create(client, '内容 B')]
    return [(identifier, '/api' if identifier == 'default' else f'/api/projects/{identifier}',
             tmp_path if identifier == 'default' else tmp_path / 'projects' / identifier)
            for identifier in identifiers]


def test_default_and_new_ips_keep_same_named_video_image_and_library_content_separate(client, tmp_path):
    spaces = three_spaces(client, tmp_path)
    for index, (_, _, root) in enumerate(spaces):
        topic = root / 'outputs' / 'same-topic'
        topic.mkdir(parents=True)
        (topic / 'video.mp4').write_bytes(f'video-{index}'.encode())
        (topic / 'cover.png').write_bytes(f'image-{index}'.encode())
        (topic / 'draft.md').write_text(f'draft-{index}')
        (topic / '.easel.json').write_text(json.dumps({'title': f'topic-{index}', 'cover': 'cover.png'}))
        exclusive = root / 'outputs' / f'only-{index}'
        exclusive.mkdir()
        (exclusive / 'private-video.mp4').write_bytes(f'private-{index}'.encode())
    for index, (_, base, _) in enumerate(spaces):
        content = client.get(base + '/outputs')
        assert content.status_code == 200, content.text
        items = {item['name']: item for item in content.json()}
        assert set(items) == {'same-topic', f'only-{index}'}
        assert items['same-topic']['meta']['title'] == f'topic-{index}'
        assert client.get(base + '/media/same-topic/video.mp4').content == f'video-{index}'.encode()
        assert client.get(base + '/media/same-topic/cover.png').content == f'image-{index}'.encode()
        assert client.get(base + '/output/same-topic/draft.md').json()['content'] == f'draft-{index}'
        for other in range(3):
            if other != index:
                assert client.get(base + f'/media/only-{other}/private-video.mp4').status_code == 404


def test_three_ip_account_statuses_do_not_fall_back_to_default_or_a_neighbor(client, tmp_path):
    spaces = three_spaces(client, tmp_path)
    (spaces[0][2] / 'cookies.json').write_text('fixture-default-bilibili')
    for index, platform in [(1, 'kuaishou'), (2, 'zhihu')]:
        login = spaces[index][2] / 'outputs' / '_login'
        login.mkdir(parents=True)
        (login / f'{platform}.json').write_text(json.dumps({'state': 'success'}))
    expected = [{'bilibili'}, {'kuaishou'}, {'zhihu'}]
    for index, (_, base, _) in enumerate(spaces):
        response = client.get(base + '/accounts')
        assert response.status_code == 200, response.text
        assert {account['platform'] for account in response.json() if account['loggedIn']} == expected[index]


def test_manual_publication_packages_and_calendar_entries_stay_in_the_current_ip(client, tmp_path):
    spaces = three_spaces(client, tmp_path)
    packages = []
    for index, (_, base, root) in enumerate(spaces):
        topic = root / 'outputs' / 'same-topic'
        topic.mkdir(parents=True)
        (topic / 'video.mp4').write_bytes(f'approved-video-{index}'.encode())
        response = client.post(base + '/manual-publish/douyin/export', json={
            'title': f'local-draft-{index}', 'body': f'content-{index}',
            'media': ['same-topic/video.mp4'], 'reviewed': True,
        })
        assert response.status_code == 200, response.text
        assert response.json()['published'] is False
        packages.append(response.json()['path'])
        created = client.post(base + '/schedule', json={'title': f'scheduled-{index}', 'date': '2026-11-04'})
        assert created.status_code == 200, created.text
    for index, (_, base, _) in enumerate(spaces):
        assert [item['title'] for item in client.get(base + '/schedule').json()] == [f'scheduled-{index}']
        package = client.get(base + '/media/' + packages[index])
        assert package.status_code == 200
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            assert json.loads(archive.read('manifest.json'))['title'] == f'local-draft-{index}'
            assert archive.read('media/01_video.mp4') == f'approved-video-{index}'.encode()
        for other, path in enumerate(packages):
            if other != index:
                assert client.get(base + '/media/' + path).status_code == 404


def test_video_project_lists_and_direct_ids_belong_to_their_original_ip(client, tmp_path):
    from easel.video_workshop import WorkshopStore

    spaces = three_spaces(client, tmp_path)
    projects = [WorkshopStore(root / 'outputs').create({'title': '同名视频', 'brief': f'own-{index}'})
                for index, (_, _, root) in enumerate(spaces)]
    for index, (_, base, _) in enumerate(spaces):
        response = client.get(base + '/workshop/projects')
        assert response.status_code == 200, response.text
        assert [item['id'] for item in response.json()['projects']] == [projects[index]['id']]
        own = client.get(base + '/workshop/projects/' + projects[index]['id'])
        assert own.status_code == 200
        assert own.json()['brief'] == f'own-{index}'
        for other, project in enumerate(projects):
            if other != index:
                assert client.get(base + '/workshop/projects/' + project['id']).status_code == 404
