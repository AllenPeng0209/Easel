from __future__ import annotations

import importlib.util
import hashlib
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient


def workshop_client(tmp_path, **callbacks):
    app = FastAPI()
    if importlib.util.find_spec("web.workshop_routes") is not None:
        from web.workshop_routes import create_router

        app.include_router(create_router(lambda: tmp_path, **callbacks))
    return TestClient(app)


def test_project_survives_new_store_instance(tmp_path):
    client = workshop_client(tmp_path)
    response = client.post("/api/workshop/projects", json={
        "title": "咖啡营销样片", "brief": "AI 初稿，人工精修", "aspect_ratio": "9:16",
        "reference_url": "https://example.com/reference",
    })
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["revision"] == 1
    assert project["stage"] == "draft"
    assert project["beeftv_canvas_id"] == ""
    reopened = workshop_client(tmp_path).get(f"/api/workshop/projects/{project['id']}")
    assert reopened.status_code == 200
    assert reopened.json()["brief"] == "AI 初稿，人工精修"
    assert workshop_client(tmp_path).get("/api/workshop/projects").json()["projects"][0]["id"] == project["id"]


def create_project(client):
    response = client.post("/api/workshop/projects", json={"title": "视频工坊验收", "brief": "保护人工修改"})
    assert response.status_code == 201, response.text
    return response.json()


def test_existing_projects_expose_empty_publication_and_archive_metadata(tmp_path):
    import json

    client = workshop_client(tmp_path)
    project = create_project(client)
    path = tmp_path / "_workshop" / "projects" / f"{project['id']}.json"
    old = json.loads(path.read_text())
    old.pop("publications", None)
    old.pop("archived_at", None)
    path.write_text(json.dumps(old))
    loaded = workshop_client(tmp_path).get("/api/workshop/projects").json()["projects"][0]
    assert loaded["publications"] == []
    assert loaded["archived_at"] is None


def test_archive_roundtrip_preserves_content_revision_and_review_history(tmp_path, video_bytes):
    client = workshop_client(tmp_path)
    project, _ = deliver(client, tmp_path, create_project(client), video_bytes)
    url = f"/api/workshop/projects/{project['id']}"
    artifact = project["deliverables"][-1]
    reviewed = client.post(url + "/reviews", json={
        "expected_revision": 2, "artifact_id": artifact["id"], "decision": "approved",
        "kind": "human", "notes": "已由调用者记录人工审阅",
    }).json()
    archived = client.post(url + "/archive", json={"expected_revision": 2, "archived": True})
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"]
    assert archived.json()["revision"] == 2
    assert archived.json()["reviews"] == reviewed["reviews"]
    assert client.get(url).json()["archived_at"] == archived.json()["archived_at"]
    restored = client.post(url + "/archive", json={"expected_revision": 2, "archived": False})
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert restored.json()["stage"] == "approved"


def test_archive_requires_current_content_revision(tmp_path):
    client = workshop_client(tmp_path)
    project = create_project(client)
    url = f"/api/workshop/projects/{project['id']}"
    client.patch(url, json={"expected_revision": 1, "brief": "新需求"})
    response = client.post(url + "/archive", json={"expected_revision": 1, "archived": True})
    assert response.status_code == 409
    assert client.get(url).json()["archived_at"] is None


def test_publication_requires_current_human_approval_then_preserves_a_versioned_record(tmp_path, video_bytes):
    client = workshop_client(tmp_path)
    project, _ = deliver(client, tmp_path, create_project(client), video_bytes)
    url = f"/api/workshop/projects/{project['id']}"
    artifact = project["deliverables"][-1]
    values = {"expected_revision": 2, "artifact_id": artifact["id"], "platform": "小红书",
              "url": "https://example.com/published-video", "published_at": "2026-10-01T08:00:00Z"}
    assert client.post(url + "/publications", json=values).status_code == 409
    client.post(url + "/reviews", json={
        "expected_revision": 2, "artifact_id": artifact["id"], "decision": "approved",
        "kind": "simulated", "notes": "模拟预审",
    })
    assert client.post(url + "/publications", json=values).status_code == 409
    client.post(url + "/reviews", json={
        "expected_revision": 2, "artifact_id": artifact["id"], "decision": "approved",
        "kind": "human", "notes": "人工核对",
    })
    response = client.post(url + "/publications", json=values)
    assert response.status_code == 200, response.text
    published = response.json()
    assert published["revision"] == 2
    assert published["stage"] == "approved"
    record = published["publications"][-1]
    assert record["revision"] == 2
    assert record["artifact_id"] == artifact["id"]
    assert record["artifact_sha256"] == artifact["sha256"]
    assert record["platform"] == "小红书"
    assert record["url"] == "https://example.com/published-video"
    assert record["published_at"].startswith("2026-10-01T08:00:00")
    assert record["id"] and record["created_at"]
    edited = client.patch(url, json={"expected_revision": 2, "brief": "下一版"}).json()
    assert edited["revision"] == 3
    assert edited["publications"] == [record]


def test_publication_cannot_reuse_approval_after_a_change_request(tmp_path, video_bytes):
    client = workshop_client(tmp_path)
    project, _ = deliver(client, tmp_path, create_project(client), video_bytes)
    url = f"/api/workshop/projects/{project['id']}"
    artifact = project["deliverables"][-1]
    for decision in ("approved", "changes_requested"):
        client.post(url + "/reviews", json={"expected_revision": 2, "artifact_id": artifact["id"],
                    "decision": decision, "kind": "human", "notes": "需调整"})
    response = client.post(url + "/publications", json={"expected_revision": 2,
        "artifact_id": artifact["id"], "platform": "抖音", "url": "https://example.com/video"})
    assert response.status_code == 409
    assert client.get(url).json()["publications"] == []


def test_publication_rejects_changed_engine_and_changed_video_bytes(tmp_path, video_bytes):
    engine = {"revision": 4}
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas-one",
                             canvas_state=lambda _project: engine)
    project = create_project(client)
    source = tmp_path / "sources" / "input.mp4"
    source.parent.mkdir()
    source.write_bytes(video_bytes)
    url = f"/api/workshop/projects/{project['id']}"
    project = client.post(url + "/deliverables", json={"expected_revision": 1,
                "path": "sources/input.mp4", "engine_revision": 4}).json()
    artifact = project["deliverables"][-1]
    assert client.post(url + "/reviews", json={"expected_revision": 2, "artifact_id": artifact["id"],
                "decision": "approved", "kind": "human", "notes": "人工核对"}).status_code == 200
    values = {"expected_revision": 2, "artifact_id": artifact["id"], "platform": "抖音",
              "url": "https://example.com/video"}
    engine["revision"] = 5
    assert client.post(url + "/publications", json=values).status_code == 409
    engine["revision"] = 4
    (tmp_path / artifact["path"]).write_bytes(b"replaced")
    assert client.post(url + "/publications", json=values).status_code == 409
    assert client.get(url).json()["publications"] == []


@pytest.mark.parametrize("value", ["file:///private/movie", "javascript:alert(1)", "https://user:secret@example.com/video"])
def test_publication_reference_must_be_a_safe_web_address(tmp_path, value):
    client = workshop_client(tmp_path)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/publications", json={
        "expected_revision": 1, "artifact_id": "a" * 32, "platform": "抖音", "url": value,
    })
    assert response.status_code == 422


@pytest.mark.parametrize("timestamp", ["2026-10-01T08:00:00", "2999-01-01T00:00:00Z"])
def test_publication_cannot_claim_a_future_or_ambiguous_publish_time(tmp_path, timestamp):
    client = workshop_client(tmp_path)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/publications", json={
        "expected_revision": 1, "artifact_id": "a" * 32, "platform": "抖音",
        "url": "https://example.com/video", "published_at": timestamp,
    })
    assert response.status_code == 422
    assert client.get(f"/api/workshop/projects/{project['id']}").json()["publications"] == []


def test_publication_and_archive_cannot_be_forged_through_content_patch(tmp_path):
    client = workshop_client(tmp_path)
    project = create_project(client)
    url = f"/api/workshop/projects/{project['id']}"
    for field, value in [("stage", "published"), ("publications", [{"url": "https://example.com/fake"}]),
                         ("archived_at", "2026-10-01T08:00:00Z")]:
        assert client.patch(url, json={"expected_revision": 1, field: value}).status_code == 422


@pytest.fixture(scope="module")
def video_bytes(tmp_path_factory):
    directory = tmp_path_factory.mktemp("workshop-media")
    path = directory / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=64x64:r=10:d=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True)
    return path.read_bytes()


def deliver(client, tmp_path, project, video_bytes):
    path = tmp_path / "sources" / "input.mp4"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(video_bytes)
    response = client.post(f"/api/workshop/projects/{project['id']}/deliverables", json={
        "expected_revision": project["revision"], "path": "sources/input.mp4",
    })
    assert response.status_code == 200, response.text
    return response.json(), path


def test_stale_edit_is_rejected_without_losing_current_content(tmp_path):
    client = workshop_client(tmp_path)
    project = create_project(client)
    url = f"/api/workshop/projects/{project['id']}"
    assert client.patch(url, json={"expected_revision": 1, "brief": "人工定稿"}).json()["revision"] == 2
    stale = client.patch(url, json={"expected_revision": 1, "brief": "AI 旧稿覆盖"})
    assert stale.status_code == 409
    assert client.get(url).json()["brief"] == "人工定稿"


def test_same_revision_writers_cannot_both_commit(tmp_path):
    client = workshop_client(tmp_path)
    project = create_project(client)
    def patch(brief):
        return workshop_client(tmp_path).patch(f"/api/workshop/projects/{project['id']}", json={
            "expected_revision": 1, "brief": brief,
        }).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(patch, ["A", "B"]))
    assert sorted(statuses) == [200, 409]


def test_delivery_is_an_immutable_copy_with_its_content_revision(tmp_path, video_bytes):
    client = workshop_client(tmp_path)
    project, source = deliver(client, tmp_path, create_project(client), video_bytes)
    artifact = project["deliverables"][-1]
    assert artifact["revision"] == project["revision"] == 2
    assert artifact["sha256"] == hashlib.sha256(video_bytes).hexdigest()
    assert artifact["path"] != "sources/input.mp4"
    source.write_bytes(b"source replaced")
    assert (tmp_path / artifact["path"]).read_bytes() == video_bytes


@pytest.mark.parametrize("relative", ["../escape.mp4", "/tmp/private.mp4", "_private/video.mp4", "project/.secret.mp4", "project/../../escape.mp4"])
def test_delivery_rejects_unsafe_paths_before_reading(tmp_path, relative):
    client = workshop_client(tmp_path)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/deliverables", json={
        "expected_revision": 1, "path": relative,
    })
    assert response.status_code == 400
    assert client.get(f"/api/workshop/projects/{project['id']}").json()["deliverables"] == []


def test_delivery_rejects_symlink_escape(tmp_path, video_bytes):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.mp4"
    outside.write_bytes(video_bytes)
    source = tmp_path / "escape.mp4"
    source.symlink_to(outside)
    client = workshop_client(tmp_path)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/deliverables", json={
        "expected_revision": 1, "path": "escape.mp4",
    })
    assert response.status_code == 400


def test_invalid_video_cannot_be_registered_as_a_delivery(tmp_path):
    (tmp_path / "broken.mp4").write_bytes(b"not a video")
    client = workshop_client(tmp_path)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/deliverables", json={
        "expected_revision": 1, "path": "broken.mp4",
    })
    assert response.status_code == 400


def test_simulated_approval_is_explicit_and_comments_do_not_invalidate_it(tmp_path, video_bytes):
    client = workshop_client(tmp_path)
    project, _ = deliver(client, tmp_path, create_project(client), video_bytes)
    url = f"/api/workshop/projects/{project['id']}"
    artifact = project["deliverables"][-1]
    reviewed = client.post(url + "/reviews", json={
        "expected_revision": 2, "artifact_id": artifact["id"], "decision": "approved",
        "kind": "simulated", "notes": "模拟人工核对字幕与声音",
    })
    assert reviewed.status_code == 200, reviewed.text
    project = reviewed.json()
    assert project["revision"] == 2
    assert project["stage"] == "simulated_reviewed"
    assert project["reviews"][-1]["artifact_sha256"] == artifact["sha256"]
    assert project["reviews"][-1]["kind"] == "simulated"
    commented = client.post(url + "/comments", json={
        "expected_revision": 2, "at_seconds": 0.1, "notes": "开头可以再快一点", "kind": "simulated",
    }).json()
    assert commented["revision"] == 2
    assert commented["stage"] == "simulated_reviewed"


def test_content_edit_requires_new_delivery_before_review(tmp_path, video_bytes):
    client = workshop_client(tmp_path)
    project, _ = deliver(client, tmp_path, create_project(client), video_bytes)
    url = f"/api/workshop/projects/{project['id']}"
    artifact = project["deliverables"][-1]
    assert client.patch(url, json={"expected_revision": 2, "brief": "新字幕稿"}).status_code == 200
    response = client.post(url + "/reviews", json={
        "expected_revision": 3, "artifact_id": artifact["id"], "decision": "approved", "kind": "human", "notes": "",
    })
    assert response.status_code == 409


def test_changed_delivery_bytes_cannot_reuse_old_hash_for_approval(tmp_path, video_bytes):
    client = workshop_client(tmp_path)
    project, _ = deliver(client, tmp_path, create_project(client), video_bytes)
    artifact = project["deliverables"][-1]
    (tmp_path / artifact["path"]).write_bytes(b"replaced output")
    response = client.post(f"/api/workshop/projects/{project['id']}/reviews", json={
        "expected_revision": 2, "artifact_id": artifact["id"], "decision": "approved", "kind": "human", "notes": "",
    })
    assert response.status_code == 409


def test_canvas_connection_failure_preserves_draft_without_leaking_details(tmp_path):
    def unavailable(_project):
        raise RuntimeError("upstream secret-token=never-display")
    client = workshop_client(tmp_path, canvas_creator=unavailable)
    project = create_project(client)
    assert project["beeftv_canvas_id"] == ""
    assert project["stage"] == "draft"
    assert "never-display" not in str(project)
    assert project["runtime_message"]


def test_real_canvas_identifier_is_persisted_from_bridge_result(tmp_path):
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas-created-upstream")
    project = create_project(client)
    assert project["beeftv_canvas_id"] == "canvas-created-upstream"
    assert workshop_client(tmp_path).get(f"/api/workshop/projects/{project['id']}").json()["beeftv_canvas_id"] == "canvas-created-upstream"


def test_runtime_is_unavailable_until_a_real_callback_confirms_it(tmp_path):
    response = workshop_client(tmp_path).get("/api/workshop/status")
    assert response.status_code == 200
    assert response.json()["state"] == "unavailable"


def test_reference_url_and_revision_are_validated(tmp_path):
    client = workshop_client(tmp_path)
    bad = client.post("/api/workshop/projects", json={"title": "Draft", "reference_url": "file:///private/secret"})
    assert bad.status_code == 422
    project = create_project(client)
    assert client.patch(f"/api/workshop/projects/{project['id']}", json={"expected_revision": True, "brief": "bad"}).status_code == 422


def test_file_replaced_after_probe_cannot_be_registered(tmp_path, video_bytes, monkeypatch):
    from easel.video_workshop import WorkshopStore

    probe = WorkshopStore._validate_video
    def probe_then_replace(path):
        probe(path)
        if path.name == "input.mp4":
            path.write_bytes(b"invalid replacement after initial media check")
    monkeypatch.setattr(WorkshopStore, "_validate_video", staticmethod(probe_then_replace))
    source = tmp_path / "sources" / "input.mp4"
    source.parent.mkdir()
    source.write_bytes(video_bytes)
    client = workshop_client(tmp_path)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/deliverables", json={
        "expected_revision": 1, "path": "sources/input.mp4",
    })
    assert response.status_code == 400
    assert client.get(f"/api/workshop/projects/{project['id']}").json()["deliverables"] == []


def test_unavailable_canvas_can_be_retried_without_recreating_local_project(tmp_path):
    available = False
    def create_canvas(_project):
        if not available:
            raise RuntimeError("unavailable")
        return "recovered-canvas"
    client = workshop_client(tmp_path, canvas_creator=create_canvas)
    project = create_project(client)
    assert project["stage"] == "draft"
    available = True
    response = client.post(f"/api/workshop/projects/{project['id']}/canvas", json={"expected_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert response.json()["beeftv_canvas_id"] == "recovered-canvas"
    assert response.json()["stage"] == "editing"
    assert len(client.get("/api/workshop/projects").json()["projects"]) == 1


def test_repeated_canvas_action_preserves_existing_remote_engineering(tmp_path):
    created = []
    def create_canvas(_project):
        created.append(f"canvas-{len(created) + 1}")
        return created[-1]
    client = workshop_client(tmp_path, canvas_creator=create_canvas)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/canvas", json={"expected_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["beeftv_canvas_id"] == "canvas-1"
    assert response.json()["revision"] == 1
    assert created == ["canvas-1"]


def test_canvas_retry_failure_preserves_revision_and_safe_message(tmp_path):
    def create_canvas(_project):
        raise RuntimeError("secret-token-do-not-show")
    client = workshop_client(tmp_path, canvas_creator=create_canvas)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/canvas", json={"expected_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert response.json()["stage"] == "draft"
    assert response.json()["runtime_message"]
    assert "secret-token" not in response.text


def test_connected_delivery_requires_exact_render_source_revision(tmp_path, video_bytes):
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "real-canvas",
                             canvas_state=lambda _project: {"revision": 7})
    project = create_project(client)
    path = tmp_path / "source.mp4"
    path.write_bytes(video_bytes)
    url = f"/api/workshop/projects/{project['id']}/deliverables"
    missing = client.post(url, json={"expected_revision": 1, "path": "source.mp4"})
    assert missing.status_code == 422
    stale = client.post(url, json={"expected_revision": 1, "path": "source.mp4", "engine_revision": 6})
    assert stale.status_code == 409
    accepted = client.post(url, json={"expected_revision": 1, "path": "source.mp4", "engine_revision": 7})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["deliverables"][-1]["engine_revision"] == 7


def test_iframe_edit_invalidates_old_render_for_review(tmp_path, video_bytes):
    revision = 7
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "real-canvas",
                             canvas_state=lambda _project: {"revision": revision})
    project = create_project(client)
    source = tmp_path / "source.mp4"
    source.write_bytes(video_bytes)
    url = f"/api/workshop/projects/{project['id']}"
    delivered = client.post(url + "/deliverables", json={
        "expected_revision": 1, "path": "source.mp4", "engine_revision": 7,
    })
    assert delivered.status_code == 200, delivered.text
    artifact = delivered.json()["deliverables"][-1]
    assert client.get(url).json()["engine_dirty"] is False
    revision = 8
    refreshed = client.get(url).json()
    assert refreshed["revision"] == 2
    assert refreshed["engine_current_revision"] == 8
    assert refreshed["engine_dirty"] is True
    response = client.post(url + "/reviews", json={
        "expected_revision": 2, "artifact_id": artifact["id"], "decision": "approved", "kind": "simulated",
    })
    assert response.status_code == 409


def test_canvas_state_failure_does_not_allow_unverified_review(tmp_path, video_bytes):
    available = True
    def canvas_state(_project):
        if not available:
            raise RuntimeError("secret-token-private")
        return {"revision": 3}
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "real-canvas", canvas_state=canvas_state)
    project = create_project(client)
    (tmp_path / "source.mp4").write_bytes(video_bytes)
    url = f"/api/workshop/projects/{project['id']}"
    delivered = client.post(url + "/deliverables", json={
        "expected_revision": 1, "path": "source.mp4", "engine_revision": 3,
    }).json()
    available = False
    response = client.post(url + "/reviews", json={
        "expected_revision": 2, "artifact_id": delivered["deliverables"][-1]["id"], "decision": "approved", "kind": "human",
    })
    assert response.status_code == 503
    assert "secret-token" not in response.text
    assert client.get(url).json()["engine_dirty"] is True


def render_client(tmp_path, video_bytes):
    state = {"engine": 7, "status": "queued", "progress": 0.0, "starts": [], "queries": 0, "collections": 0}
    def start(project, operation_id):
        state["starts"].append(operation_id)
        return {"task_id": "upstream-task", "engine_revision": project["engine_current_revision"], "status": "queued"}
    def status(_project, _receipt):
        state["queries"] += 1
        return {"status": state["status"], "progress": state["progress"]}
    def collect(_project, receipt):
        state["collections"] += 1
        path = tmp_path / "render-results" / f"{receipt['id']}.mp4"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(video_bytes)
        return {"path": path.relative_to(tmp_path).as_posix(), "engine_revision": receipt["engine_revision"]}
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "real-canvas",
                             canvas_state=lambda _project: {"revision": state["engine"]},
                             render_start=start, render_status=status, render_collect=collect)
    return client, state


@pytest.mark.parametrize("terminal", ["succeeded", "failed"])
def test_project_list_refreshes_an_active_render_without_submitting_or_collecting(tmp_path, video_bytes, terminal):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    receipt = client.post(base + "/renders", json={"expected_revision": 1}).json()
    state.update(status=terminal, progress=1.0 if terminal == "succeeded" else 0.0)
    response = client.get("/api/workshop/projects")
    assert response.status_code == 200
    observed = response.json()["projects"][0]
    assert observed["renders"][-1]["id"] == receipt["id"]
    assert observed["renders"][-1]["status"] == terminal
    assert observed["deliverables"] == []
    assert observed["revision"] == 1
    assert len(state["starts"]) == 1
    assert state["collections"] == 0
    assert state["queries"] == 1
    # A terminal task does not keep hitting the runtime on every list refresh.
    client.get("/api/workshop/projects")
    assert state["queries"] == 1


def test_project_list_remains_available_when_one_active_runtime_query_fails(tmp_path, video_bytes):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    receipt = client.post(f"/api/workshop/projects/{project['id']}/renders", json={"expected_revision": 1}).json()
    def unavailable(_project, _receipt):
        raise RuntimeError("upstream credential=private")
    reader = workshop_client(tmp_path, render_status=unavailable)
    response = reader.get("/api/workshop/projects")
    assert response.status_code == 200
    observed = response.json()["projects"][0]
    assert observed["renders"][-1] == receipt
    assert "private" not in response.text
    assert len(state["starts"]) == 1


def test_render_submission_is_idempotent_and_get_only_observes(tmp_path, video_bytes):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    url = f"/api/workshop/projects/{project['id']}/renders"
    first = client.post(url, json={"expected_revision": 1})
    assert first.status_code == 200, first.text
    receipt = first.json()
    assert receipt["engine_revision"] == 7
    assert receipt["content_revision"] == 1
    assert receipt["status"] == "queued"
    second = client.post(url, json={"expected_revision": 1})
    assert second.json()["id"] == receipt["id"]
    assert state["starts"] == [receipt["operation_id"]]
    assert len(receipt["operation_id"]) <= 128
    state.update(status="running", progress=0.25)
    polled = client.get(url + "/" + receipt["id"])
    assert polled.status_code == 200
    assert polled.json()["progress"] == 0.25
    assert state["queries"] == 1
    assert len(state["starts"]) == 1
    assert client.get(f"/api/workshop/projects/{project['id']}").json()["deliverables"] == []


def test_completed_render_can_be_collected_once_and_repeated_safely(tmp_path, video_bytes):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    receipt = client.post(base + "/renders", json={"expected_revision": 1}).json()
    state.update(status="succeeded", progress=1.0)
    assert client.get(base + "/renders/" + receipt["id"]).json()["status"] == "succeeded"
    collected = client.post(base + "/renders/" + receipt["id"] + "/collect", json={"expected_revision": 1})
    assert collected.status_code == 200, collected.text
    delivered = collected.json()
    assert delivered["revision"] == 2
    assert len(delivered["deliverables"]) == 1
    artifact = delivered["deliverables"][0]
    saved_receipt = delivered["renders"][0]
    assert saved_receipt["artifact_id"] == artifact["id"]
    assert saved_receipt["collected_revision"] == 2
    assert artifact["engine_revision"] == 7
    assert artifact["render_id"] == receipt["id"]
    repeated = client.post(base + "/renders/" + receipt["id"] + "/collect", json={"expected_revision": 1})
    assert repeated.status_code == 200
    assert repeated.json()["revision"] == 2
    assert len(repeated.json()["deliverables"]) == 1
    assert state["collections"] == 1
    assert client.post(base + "/renders", json={"expected_revision": 2}).json()["id"] == receipt["id"]
    assert len(state["starts"]) == 1


def test_pending_render_cannot_be_collected_as_a_delivery(tmp_path, video_bytes):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    receipt = client.post(base + "/renders", json={"expected_revision": 1}).json()
    response = client.post(base + "/renders/" + receipt["id"] + "/collect", json={"expected_revision": 1})
    assert response.status_code == 409
    assert state["collections"] == 0


@pytest.mark.parametrize("change", ["engine", "local"])
def test_render_cannot_be_adopted_after_project_changes(tmp_path, video_bytes, change):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    receipt = client.post(base + "/renders", json={"expected_revision": 1}).json()
    state.update(status="succeeded", progress=1.0)
    client.get(base + "/renders/" + receipt["id"])
    if change == "engine":
        state["engine"] = 8
        expected = 1
    else:
        assert client.patch(base, json={"expected_revision": 1, "brief": "改过的营销稿"}).status_code == 200
        expected = 2
    response = client.post(base + "/renders/" + receipt["id"] + "/collect", json={"expected_revision": expected})
    assert response.status_code == 409
    assert state["collections"] == 0
    assert client.get(base).json()["deliverables"] == []


def test_missing_render_backend_is_reported_without_fake_receipt(tmp_path):
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas", canvas_state=lambda _project: {"revision": 1})
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    response = client.post(base + "/renders", json={"expected_revision": 1})
    assert response.status_code == 503
    assert client.get(base).json().get("renders", []) == []


def test_render_callback_result_cannot_change_source_revision(tmp_path, video_bytes):
    def start(_project, _operation_id):
        return {"task_id": "render-wrong-revision", "engine_revision": 8, "status": "succeeded"}
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas", canvas_state=lambda _project: {"revision": 7},
                             render_start=start, render_status=lambda _project, _receipt: {"status": "succeeded"},
                             render_collect=lambda _project, _receipt: pytest.fail("unverified result must not be collected"))
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    response = client.post(base + "/renders", json={"expected_revision": 1})
    assert response.status_code == 409
    saved = client.get(base).json()
    assert saved["deliverables"] == []
    assert saved["renders"][0]["task_id"] == "render-wrong-revision"
    assert saved["renders"][0]["engine_revision"] == 7
    assert saved["renders"][0]["reported_engine_revision"] == 8


def test_failed_render_submission_keeps_operation_identity_without_get_resubmission(tmp_path):
    starts = []
    def start(_project, operation_id):
        starts.append(operation_id)
        raise RuntimeError("secret-token-upstream")
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas", canvas_state=lambda _project: {"revision": 4},
                             render_start=start, render_status=lambda _project, _receipt: {"status": "failed"},
                             render_collect=lambda _project, _receipt: pytest.fail("failed submission cannot collect"))
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    response = client.post(base + "/renders", json={"expected_revision": 1})
    assert response.status_code == 503
    assert "secret-token" not in response.text
    receipt = client.get(base).json()["renders"][0]
    assert receipt["status"] == "failed"
    polled = client.get(base + "/renders/" + receipt["id"])
    assert polled.json()["status"] == "failed"
    assert receipt["submission_state"] == "unconfirmed"
    assert starts == [receipt["operation_id"]]


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_explicit_start_retries_confirmed_terminal_task_with_a_new_attempt(tmp_path, video_bytes, terminal_status):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    first = client.post(base + "/renders", json={"expected_revision": 1}).json()
    state["status"] = terminal_status
    assert client.get(base + "/renders/" + first["id"]).json()["status"] == terminal_status

    retried = client.post(base + "/renders", json={"expected_revision": 1})
    assert retried.status_code == 200, retried.text
    second = retried.json()
    assert second["id"] != first["id"]
    assert second["operation_id"] != first["operation_id"]
    assert second["status"] == "queued"
    assert second["content_revision"] == 1
    assert second["engine_revision"] == 7
    assert len(client.get(base).json()["renders"]) == 2
    assert len(state["starts"]) == 2
    # A double-click while the new attempt is active reuses it.
    assert client.post(base + "/renders", json={"expected_revision": 1}).json()["id"] == second["id"]
    assert len(state["starts"]) == 2


def test_explicit_retry_reconciles_an_unknown_submission_with_its_original_operation(tmp_path):
    operations = {}
    calls = []
    def start(_project, operation_id):
        calls.append(operation_id)
        # The upstream accepted the first call, but the response was lost.
        task = operations.setdefault(operation_id, {"task_id": "accepted-before-timeout", "engine_revision": 4,
                                                   "status": "queued"})
        if len(calls) == 1:
            raise TimeoutError("response lost")
        return task
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas",
                             canvas_state=lambda _project: {"revision": 4}, render_start=start)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    assert client.post(base + "/renders", json={"expected_revision": 1}).status_code == 503
    unknown = client.get(base).json()["renders"][0]
    assert client.get(base + "/renders/" + unknown["id"]).status_code == 200
    assert len(calls) == 1

    recovered = client.post(base + "/renders", json={"expected_revision": 1})
    assert recovered.status_code == 200, recovered.text
    receipt = recovered.json()
    assert receipt["id"] == unknown["id"]
    assert receipt["operation_id"] == unknown["operation_id"]
    assert receipt["task_id"] == "accepted-before-timeout"
    assert receipt["status"] == "queued"
    assert receipt["submission_state"] == "confirmed"
    assert calls == [unknown["operation_id"], unknown["operation_id"]]
    assert len(operations) == 1
    assert len(client.get(base).json()["renders"]) == 1


def test_changed_local_content_gets_a_distinct_upstream_render_operation(tmp_path, video_bytes):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    first = client.post(base + "/renders", json={"expected_revision": 1}).json()
    assert client.patch(base, json={"expected_revision": 1, "brief": "修改口播与营销承诺"}).status_code == 200
    second = client.post(base + "/renders", json={"expected_revision": 2}).json()
    assert second["id"] != first["id"]
    assert second["engine_revision"] == first["engine_revision"]
    assert second["operation_id"] != first["operation_id"]
    assert second["content_revision"] == 2
    assert len(set(state["starts"])) == 2


def test_explicit_retry_recovers_an_interrupted_saved_render_intent(tmp_path):
    from easel.video_workshop import WorkshopStore

    operations = []
    def interrupted(_project, operation_id):
        operations.append(operation_id)
        raise KeyboardInterrupt("process interrupted after the upstream may have accepted")
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas",
                             canvas_state=lambda _project: {"revision": 4},
                             render_start=lambda _project, operation_id: {
                                 "task_id": "recovered-task", "engine_revision": 4, "status": "running",
                             } if operation_id == operations[0] else pytest.fail("must retain original operation"))
    project = create_project(client)
    with pytest.raises(KeyboardInterrupt):
        WorkshopStore(tmp_path).start_render(project["id"], 1, canvas_state=lambda _project: {"revision": 4},
                                           render_start=interrupted)
    base = f"/api/workshop/projects/{project['id']}"
    original = client.get(base).json()["renders"][0]
    assert original["task_id"] == ""
    # On reopening the UI, an abandoned local submission becomes explicitly
    # retryable; observing it never calls upstream render_start.
    observed = client.get(base + "/renders/" + original["id"])
    assert observed.json()["status"] == "failed"
    assert observed.json()["submission_state"] == "unconfirmed"
    assert observed.json()["submission_attempts"][0]["error_code"] == "submission_interrupted"
    assert operations == [original["operation_id"]]
    recovered = client.post(base + "/renders", json={"expected_revision": 1})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["id"] == original["id"]
    assert recovered.json()["task_id"] == "recovered-task"
    assert recovered.json()["submission_state"] == "confirmed"
    assert len(client.get(base).json()["renders"]) == 1


def test_concurrent_explicit_retries_do_not_duplicate_an_unknown_submission(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def start(_project, operation_id):
        calls.append(operation_id)
        if len(calls) == 1:
            raise TimeoutError("response lost")
        entered.set()
        assert release.wait(5), "test did not release the upstream call"
        return {"task_id": "only-task", "engine_revision": 4, "status": "running"}
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas",
                             canvas_state=lambda _project: {"revision": 4}, render_start=start)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    assert client.post(base + "/renders", json={"expected_revision": 1}).status_code == 503
    receipt = client.get(base).json()["renders"][0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(client.post, base + "/renders", json={"expected_revision": 1})
        try:
            assert entered.wait(2), "explicit retry never reached the original upstream operation"
            concurrent = client.post(base + "/renders", json={"expected_revision": 1})
            assert concurrent.status_code == 200, concurrent.text
            assert concurrent.json()["id"] == receipt["id"]
        finally:
            release.set()
        recovered = future.result(timeout=5)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["task_id"] == "only-task"
    assert calls == [receipt["operation_id"], receipt["operation_id"]]
    assert len(recovered.json()["submission_attempts"]) == 2
    assert len(client.get(base).json()["renders"]) == 1


def test_unknown_task_cannot_be_confirmed_by_polling_during_explicit_reconciliation(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    operations = []
    def start(_project, operation_id):
        operations.append(operation_id)
        if len(operations) == 1:
            return {"task_id": "wrong-source-task", "engine_revision": 9, "status": "succeeded"}
        entered.set()
        assert release.wait(5), "test did not release the upstream call"
        return {"task_id": "correct-source-task", "engine_revision": 4, "status": "queued"}
    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas",
                             canvas_state=lambda _project: {"revision": 4}, render_start=start,
                             render_status=lambda _project, _receipt: {"status": "succeeded"})
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    assert client.post(base + "/renders", json={"expected_revision": 1}).status_code == 409
    original = client.get(base).json()["renders"][0]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, base + "/renders", json={"expected_revision": 1})
        try:
            assert entered.wait(2)
            polled = client.get(base + "/renders/" + original["id"])
            assert polled.status_code == 200, polled.text
            assert polled.json()["status"] != "succeeded"
            assert polled.json()["submission_state"] == "unconfirmed"
        finally:
            release.set()
        recovered = future.result(timeout=5)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["task_id"] == "correct-source-task"
    assert recovered.json()["submission_state"] == "confirmed"
    assert operations == [original["operation_id"], original["operation_id"]]


def test_render_retry_freezes_attempts_and_reuses_only_unconfirmed_operation(tmp_path):
    from easel.video_workshop import WorkshopError, WorkshopStore

    operations = []

    def start(_project, operation_id):
        operations.append(operation_id)
        if len(operations) == 1:
            raise WorkshopError("unknown-credential-value-from-provider", 503)
        return {"task_id": f"task-{len(operations)}", "engine_revision": 4,
                "status": "failed" if len(operations) == 2 else "succeeded",
                "message": "unknown-credential-value-from-provider"}

    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas",
                             canvas_state=lambda _project: {"revision": 4}, render_start=start)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    original = client.post(base + "/renders", json={"expected_revision": 1})
    assert original.status_code == 503
    first = client.get(base).json()["renders"][0]
    prior = first["submission_attempts"]
    assert len(prior) == 1
    assert prior[0]["status"] == "unconfirmed" and prior[0]["error_code"] == "submission_unconfirmed"
    assert "unknown-credential-value" not in original.text
    assert "unknown-credential-value" not in str(prior)

    failed = client.post(base + "/renders", json={"expected_revision": 1}).json()
    assert failed["status"] == "failed" and failed["submission_state"] == "confirmed"
    assert "unknown-credential-value" not in str(failed)
    assert failed["id"] == first["id"]
    assert failed["submission_attempts"][0] == prior[0]
    assert failed["submission_attempts"][1]["task_id"] == "task-2"
    assert [attempt["number"] for attempt in failed["submission_attempts"]] == [1, 2]

    recovered = client.post(base + "/renders", json={"expected_revision": 1}).json()
    assert recovered["id"] != failed["id"]
    assert recovered["submission_attempts"][0]["number"] == 1
    assert operations == [failed["operation_id"], failed["operation_id"], recovered["operation_id"]]
    assert WorkshopStore(tmp_path).get(project["id"])["renders"][0] == failed
    assert client.post(base + "/renders", json={"expected_revision": 1}).json() == recovered
    assert len(operations) == 3


def test_render_progress_unknown_stays_distinct_from_zero_and_terminal_time_is_fixed(tmp_path, video_bytes):
    client, state = render_client(tmp_path, video_bytes)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    initial = client.post(base + "/renders", json={"expected_revision": 1}).json()
    route = base + "/renders/" + initial["id"]
    assert initial["progress_known"] is False
    assert "completed_at" not in initial
    state.update(status="running", progress=0.0)
    zero = client.get(route).json()
    assert zero["progress_known"] is True and zero["progress"] == 0.0
    state.update(progress=0.45)
    client.get(route)
    state.update(progress=None)
    running = client.get(route).json()
    assert running["progress_known"] is True and running["progress"] == 0.45
    assert "completed_at" not in running

    state.update(status="succeeded")
    terminal = client.get(route).json()
    assert terminal["status"] == "succeeded"
    assert terminal["completed_at"] == terminal["updated_at"]
    assert terminal["progress_known"] is False
    collected = client.post(route + "/collect", json={"expected_revision": 1})
    assert collected.status_code == 200
    assert client.get(route).json()["completed_at"] == terminal["completed_at"]


def test_unconfirmed_submission_does_not_get_terminal_time(tmp_path):
    def lost(_project, _operation):
        raise TimeoutError("not confirmed")

    client = workshop_client(tmp_path, canvas_creator=lambda _project: "canvas",
                             canvas_state=lambda _project: {"revision": 4}, render_start=lost)
    project = create_project(client)
    base = f"/api/workshop/projects/{project['id']}"
    assert client.post(base + "/renders", json={"expected_revision": 1}).status_code == 503
    receipt = client.get(base).json()["renders"][0]
    assert receipt["submission_state"] == "unconfirmed"
    assert receipt["progress_known"] is False
    assert "completed_at" not in receipt
