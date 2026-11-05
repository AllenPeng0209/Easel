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
    assert response.status_code in {400, 403, 422}
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
    assert response.status_code in {400, 403, 422}


def test_invalid_video_cannot_be_registered_as_a_delivery(tmp_path):
    (tmp_path / "broken.mp4").write_bytes(b"not a video")
    client = workshop_client(tmp_path)
    project = create_project(client)
    response = client.post(f"/api/workshop/projects/{project['id']}/deliverables", json={
        "expected_revision": 1, "path": "broken.mp4",
    })
    assert response.status_code in {400, 403, 422}


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
    assert response.status_code in {400, 403, 422}
    assert client.get(f"/api/workshop/projects/{project['id']}").json()["deliverables"] == []
